from datetime import date

from decimal import Decimal

from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.approvals.models import DocumentType
from apps.approvals.services import ensure_approved
from apps.core.api import CompanyReadOnlyViewSet, CompanyViewSet
from apps.core.exceptions import BusinessError
from apps.iam.access import ErpPermission, has_perm, require, resolve_company

from . import reports, services
from .models import (Account, EntryState, FiscalPeriod, FiscalYear, Journal, JournalEntry, JournalLine, PeriodState,
                     Tax)
from .serializers import (AccountingSettingsSerializer, AccountSerializer, FiscalPeriodSerializer,
                          FiscalYearSerializer, JournalEntrySerializer, JournalSerializer, TaxSerializer)


def parse_date(value, default=None, name="date"):
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise drf_serializers.ValidationError({name: "Use YYYY-MM-DD."})


class AccountViewSet(CompanyViewSet):
    queryset = Account.objects.select_related("parent")
    serializer_class = AccountSerializer
    perms = {"list": "ledger.account.view", "tree": "ledger.account.view", "balances": "ledger.account.view",
             "*": "ledger.account.manage"}
    filterset_fields = ["account_class", "account_type", "active", "parent", "level", "reconcilable"]
    search_fields = ["code", "name"]
    ordering = ["code"]
    pagination_class = None

    def perform_destroy(self, instance):
        if instance.lines.exists() or instance.children.exists():
            raise BusinessError("Account has postings or sub-accounts; deactivate it instead.", "in_use")
        instance.delete()

    @action(detail=False)
    def tree(self, request):
        """Whole chart as a nested tree with closing balances (base currency)."""
        as_of = parse_date(request.query_params.get("as_of"), timezone.localdate())
        tb = reports.trial_balance(self.company, date(1900, 1, 1), as_of, hide_zero=False)
        bal = {r["account_id"]: r["closing"] for r in tb["rows"]}
        nodes = {}
        for a in Account.objects.filter(company=self.company).order_by("code"):
            nodes[a.pk] = {"id": a.pk, "code": a.code, "name": a.name, "level": a.level, "is_group": a.is_group,
                           "account_class": a.account_class, "account_type": a.account_type, "active": a.active,
                           "is_control": a.is_control, "reconcilable": a.reconcilable,
                           "balance": bal.get(a.pk, "0"), "parent": a.parent_id, "children": []}
        roots = []
        for n in nodes.values():
            (nodes[n["parent"]]["children"] if n["parent"] else roots).append(n)
        return Response(roots)


class JournalViewSet(CompanyViewSet):
    queryset = Journal.objects.all()
    serializer_class = JournalSerializer
    perms = {"list": ["ledger.entry.view", "billing.payment.view"], "*": "ledger.journal.manage"}
    filterset_fields = ["type", "active"]
    pagination_class = None


class TaxViewSet(CompanyViewSet):
    queryset = Tax.objects.all()
    serializer_class = TaxSerializer
    perms = {"list": "authenticated", "*": "ledger.tax.manage"}
    filterset_fields = ["scope", "active"]
    pagination_class = None


class FiscalYearViewSet(CompanyReadOnlyViewSet):
    queryset = FiscalYear.objects.prefetch_related("periods")
    serializer_class = FiscalYearSerializer
    perms = {"list": "ledger.entry.view", "open_year": "ledger.period.manage", "close_year": "ledger.year.close"}
    http_method_names = ["get", "post", "head", "options"]
    pagination_class = None

    @action(detail=False, methods=["post"], url_path="open")
    def open_year(self, request):
        try:
            year = int(request.data.get("start_year"))
        except (TypeError, ValueError):
            raise drf_serializers.ValidationError({"start_year": "Give the calendar year the fiscal year starts in."})
        fy = services.create_fiscal_year(self.company, year, request.user)
        return Response(FiscalYearSerializer(fy).data, status=201)

    @action(detail=True, methods=["post"])
    def close_year(self, request, pk=None):
        fy = self.get_object()
        entry = services.close_fiscal_year(fy, request.user)
        return Response({"closed": True, "closing_entry": entry.number if entry else None})


class FiscalPeriodViewSet(CompanyReadOnlyViewSet):
    queryset = FiscalPeriod.objects.all()
    serializer_class = FiscalPeriodSerializer
    perms = {"list": "ledger.entry.view", "set_state": ["ledger.period.close", "ledger.period.lock"]}
    http_method_names = ["get", "post", "head", "options"]
    filterset_fields = ["fiscal_year", "state"]
    pagination_class = None

    @action(detail=True, methods=["post"])
    def set_state(self, request, pk=None):
        state = request.data.get("state")
        if state not in PeriodState.values:
            raise drf_serializers.ValidationError({"state": f"One of {PeriodState.values}"})
        p = services.set_period_state(self.get_object(), state, request.user)
        return Response(FiscalPeriodSerializer(p).data)


class JournalEntryViewSet(CompanyViewSet):
    queryset = (JournalEntry.objects.select_related("journal", "posted_by", "created_by", "reversal_of")
                .prefetch_related("lines__account", "lines__partner"))
    serializer_class = JournalEntrySerializer
    perms = {"list": "ledger.entry.view", "create": "ledger.entry.create", "update": "ledger.entry.create",
             "destroy": "ledger.entry.create", "post": "ledger.entry.post", "reverse": "ledger.entry.reverse"}
    filterset_fields = {"state": ["exact"], "journal": ["exact"], "date": ["gte", "lte", "exact"],
                        "source_type": ["exact"]}
    search_fields = ["number", "reference", "narration"]
    ordering_fields = ["date", "number", "total_debit"]

    def get_queryset(self):
        qs = super().get_queryset()
        acc = self.request.query_params.get("account")
        if acc:
            qs = qs.filter(lines__account_id=acc).distinct()
        return qs

    def _specs(self, company, lines, currency, rate, on_date):
        specs = [services.LineSpec(account=ln["account"], debit=ln.get("debit") or 0, credit=ln.get("credit") or 0,
                                   partner=ln.get("partner"), description=ln.get("description", ""),
                                   cost_center=ln.get("cost_center"), tax=ln.get("tax"),
                                   due_date=ln.get("due_date")) for ln in lines]
        rate = services.resolve_rate(company, currency, on_date, rate)
        return services.lines_from_document_amounts(company, rate, specs), rate

    def create_document(self, request, *args, **kwargs):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        company = self.company
        currency = getattr(v.get("currency"), "code", None) or company.base_currency_id
        specs, rate = self._specs(company, v["lines"], currency, v.get("rate"), v["date"])
        entry = services.create_entry(company, v["journal"], v["date"], specs, user=request.user,
                                      reference=v.get("reference", ""), narration=v.get("narration", ""),
                                      currency=currency, rate=rate)
        return Response(self.get_serializer(entry).data, status=201)

    def update(self, request, *args, **kwargs):
        entry = self.get_object()
        if entry.state != EntryState.DRAFT or entry.source_type:
            raise BusinessError("Only manual draft entries can be edited.", "not_draft")
        s = self.get_serializer(entry, data=request.data, partial=kwargs.get("partial", False))
        s.is_valid(raise_exception=True)
        v = s.validated_data
        for f in ("journal", "date", "reference", "narration"):
            if f in v:
                setattr(entry, f, v[f])
        if "currency" in v:
            entry.currency = v["currency"]
        if "lines" in v:
            specs, rate = self._specs(self.company, v["lines"], entry.currency_id, v.get("rate"), entry.date)
            entry.rate = rate
            services.replace_lines(entry, specs)
        entry.save()
        return Response(self.get_serializer(entry).data)

    def perform_destroy(self, instance):
        if instance.state not in (EntryState.DRAFT,) or instance.source_type:
            raise BusinessError("Only manual draft entries can be deleted.", "not_draft")
        instance.delete()

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        entry = self.get_object()
        amount = entry.lines.aggregate(t=Sum("debit"))["t"] or 0
        if entry.is_manual and not ensure_approved(self.company, DocumentType.JOURNAL_ENTRY, entry, amount,
                                                   request.user):
            return Response(self.get_serializer(JournalEntry.objects.get(pk=entry.pk)).data, status=202)
        entry = services.post_entry(entry, request.user)
        return Response(self.get_serializer(entry).data)

    @action(detail=True, methods=["post"])
    def reverse(self, request, pk=None):
        entry = self.get_object()
        rev = services.reverse_entry(entry, request.user, parse_date(request.data.get("date")),
                                     request.data.get("reason", ""))
        return Response(self.get_serializer(rev).data, status=201)


class AccountingSettingsView(APIView):
    permission_classes = [ErpPermission]
    perms = {"get": "ledger.account.view", "patch": "ledger.account.manage"}

    def get(self, request):
        return Response(AccountingSettingsSerializer(services.settings_for(resolve_company(request))).data)

    def patch(self, request):
        company = resolve_company(request)
        s = AccountingSettingsSerializer(services.settings_for(company), data=request.data, partial=True,
                                         context={"company": company, "request": request})
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data)


class ReportView(APIView):
    """GET /api/reports/<name>/"""
    permission_classes = [ErpPermission]
    perms = {"get": "ledger.report.view"}

    def get(self, request, name):
        company = resolve_company(request)
        q = request.query_params
        today = timezone.localdate()
        year_start = date(today.year, company.fiscal_year_start_month, 1)
        if year_start > today:
            year_start = date(today.year - 1, company.fiscal_year_start_month, 1)
        dfrom = parse_date(q.get("date_from"), year_start, "date_from")
        dto = parse_date(q.get("date_to"), today, "date_to")
        level = int(q["level"]) if q.get("level", "").isdigit() else None
        if name == "trial-balance":
            return Response(reports.trial_balance(company, dfrom, dto, max_level=level,
                                                  hide_zero=q.get("hide_zero", "1") != "0"))
        if name == "general-ledger":
            acc = get_object_or_404(Account, pk=q.get("account"), company=company)
            partner = q.get("partner") or None
            return Response(reports.general_ledger(company, acc, dfrom, dto, partner=partner))
        if name == "balance-sheet":
            return Response(reports.balance_sheet(company, parse_date(q.get("as_of"), today, "as_of"), level))
        if name == "income-statement":
            return Response(reports.income_statement(company, dfrom, dto, level))
        if name in ("ar-aging", "ap-aging"):
            from apps.billing.services import aging
            return Response(aging(company, "receivable" if name == "ar-aging" else "payable",
                                  parse_date(q.get("as_of"), today, "as_of")))
        if name == "subledger-reconciliation":
            from apps.billing.services import subledger_reconciliation
            return Response(subledger_reconciliation(company, parse_date(q.get("as_of"), today, "as_of")))
        if name == "inventory-valuation":
            require(request, "inventory.valuation.view")
            from apps.inventory.services import valuation_report
            data = valuation_report(company)
            gl_inv = JournalLine.objects.filter(company=company, posted=True, account__account_type="inventory")
            agg = gl_inv.aggregate(d=Sum("debit"), c=Sum("credit"))
            data["general_ledger_value"] = str((agg["d"] or 0) - (agg["c"] or 0))
            return Response(data)
        if name == "vat":
            return Response(vat_report(company, dfrom, dto))
        raise drf_serializers.ValidationError({"report": "Unknown report."})


def vat_report(company, dfrom, dto):
    """Output VAT (sales) and input VAT (purchases) by tax code, with the taxable base."""
    rows = []
    output_tax = input_tax = Decimal(0)
    for tax in Tax.objects.filter(company=company).select_related("account"):
        agg = (JournalLine.objects.filter(company=company, posted=True, date__gte=dfrom, date__lte=dto, tax=tax,
                                          account=tax.account)
               .aggregate(d=Sum("debit"), c=Sum("credit"), b=Sum("tax_base")))
        amt = (agg["c"] or 0) - (agg["d"] or 0)
        if tax.scope == "purchase":
            amt = -amt
            input_tax += amt
        else:
            output_tax += amt
        if amt or agg["b"]:
            rows.append({"tax": tax.code, "name": tax.name, "scope": tax.scope, "rate": str(tax.rate),
                         "taxable_base": str(agg["b"] or 0), "tax_amount": str(amt)})
    return {"date_from": dfrom, "date_to": dto, "rows": rows, "output_tax": str(output_tax),
            "input_tax": str(input_tax), "net_payable": str(output_tax - input_tax)}
