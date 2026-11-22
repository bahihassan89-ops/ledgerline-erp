from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import CompanyViewSet
from apps.core.exceptions import BusinessError
from apps.integrations.events import emit
from apps.ledger.models import JournalLine
from apps.ledger.views import parse_date

from .models import Partner, PartnerBankChangeRequest, PaymentTerm
from .serializers import PartnerBankChangeRequestSerializer, PartnerSerializer, PaymentTermSerializer
from .services import apply_bank_change, request_bank_change, sync_intercompany_mirror, verify_bank_change


class PaymentTermViewSet(CompanyViewSet):
    queryset = PaymentTerm.objects.all()
    serializer_class = PaymentTermSerializer
    perms = {"list": "partners.partner.view", "*": "partners.partner.manage"}
    pagination_class = None


class PartnerViewSet(CompanyViewSet):
    queryset = Partner.objects.select_related("payment_term")
    serializer_class = PartnerSerializer
    perms = {"list": "partners.partner.view", "statement": "partners.partner.view", "*": "partners.partner.manage"}
    filterset_fields = ["is_customer", "is_vendor", "active", "on_hold"]
    search_fields = ["code", "name", "email", "tax_number"]
    ordering_fields = ["code", "name"]

    def perform_create(self, serializer):
        p = serializer.save(company=self.company, created_by=self.request.user)
        sync_intercompany_mirror(p)
        emit(self.company, "partner.created", PartnerSerializer(p).data)

    def perform_update(self, serializer):
        p = serializer.save()
        sync_intercompany_mirror(p)
        emit(self.company, "partner.updated", PartnerSerializer(p).data)

    def perform_destroy(self, instance):
        if instance.journal_lines.exists() or instance.invoices.exists():
            raise BusinessError("Partner has transactions; deactivate instead.", "in_use")
        instance.delete()

    @action(detail=True)
    def statement(self, request, pk=None):
        """Running statement of the partner's receivable/payable accounts."""
        p = self.get_object()
        today = timezone.localdate()
        dfrom = parse_date(request.query_params.get("date_from"), today.replace(month=1, day=1))
        dto = parse_date(request.query_params.get("date_to"), today)
        base = JournalLine.objects.filter(company=self.company, partner=p, posted=True,
                                          account__account_type__in=["receivable", "payable"])
        o = base.filter(date__lt=dfrom).aggregate(d=Sum("debit"), c=Sum("credit"))
        bal = (o["d"] or 0) - (o["c"] or 0)
        opening, rows = bal, []
        for ln in base.filter(date__gte=dfrom, date__lte=dto).select_related("entry").order_by("date", "id"):
            bal += ln.debit - ln.credit
            rows.append({"date": ln.date, "number": ln.entry.number, "reference": ln.entry.reference,
                         "description": ln.entry.narration, "debit": str(ln.debit), "credit": str(ln.credit),
                         "balance": str(bal), "due_date": ln.due_date})
        return Response({"partner": {"id": p.pk, "code": p.code, "name": p.name}, "opening_balance": str(opening),
                         "closing_balance": str(bal), "rows": rows,
                         "note": "Positive balance = partner owes us; negative = we owe the partner."})


class PartnerBankChangeRequestViewSet(CompanyViewSet):
    """Maker-checker workflow for vendor/customer bank-detail changes (item #34). Creating a request
    here never touches Partner.bank_* -- it only takes effect once `decide` (the generic
    apps.approvals endpoint) approves it AND `verify`/`apply` below have both run. See
    apps.partners.services for the full rationale."""
    queryset = PartnerBankChangeRequest.objects.select_related("partner", "verification")
    serializer_class = PartnerBankChangeRequestSerializer
    http_method_names = ["get", "post", "head", "options"]
    perms = {"list": "partners.partner.view", "retrieve": "partners.partner.view",
             "create": "partners.partner.manage", "verify": "partners.bank_change.verify",
             "apply": "partners.partner.manage"}
    filterset_fields = ["partner", "state"]

    def create(self, request, *args, **kwargs):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        partner = get_object_or_404(Partner, pk=v["partner"].pk, company=self.company)
        req = request_bank_change(
            partner, request.user, bank_name=v.get("bank_name", ""),
            bank_account_number=v.get("bank_account_number", ""), iban=v.get("iban", ""),
            swift_bic=v.get("swift_bic", ""), bank_country=v.get("bank_country", ""),
            reason_code=v.get("reason_code", ""), reason=v.get("reason", ""))
        return Response(self.get_serializer(req).data, status=201)

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        req = self.get_object()
        verify_bank_change(req, request.user, request.data.get("phone_called", ""),
                           notes=request.data.get("notes", ""))
        return Response(self.get_serializer(req).data)

    @action(detail=True, methods=["post"])
    def apply(self, request, pk=None):
        apply_bank_change(self.get_object(), request.user)
        return Response(self.get_serializer(self.get_object()).data)
