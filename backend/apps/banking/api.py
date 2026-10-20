from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import CompanyReadOnlyViewSet
from apps.ledger.models import Account, Journal, JournalLine
from apps.ledger.views import parse_date
from apps.partners.models import Partner

from . import services
from .models import BankStatement, BankStatementLine


class StatementLineSerializer(serializers.ModelSerializer):
    matched_entry = serializers.CharField(source="matched_line.entry.number", read_only=True, default=None)

    class Meta:
        model = BankStatementLine
        fields = ["id", "date", "description", "reference", "amount", "partner", "matched_line", "matched_entry",
                  "matched_by"]


class StatementSerializer(serializers.ModelSerializer):
    lines = StatementLineSerializer(many=True, read_only=True)
    journal_code = serializers.CharField(source="journal.code", read_only=True)
    unmatched = serializers.SerializerMethodField()

    class Meta:
        model = BankStatement
        fields = ["id", "journal", "journal_code", "reference", "date_from", "date_to", "opening_balance",
                  "closing_balance", "state", "unmatched", "lines"]

    def get_unmatched(self, obj):
        return sum(1 for l in obj.lines.all() if not l.matched_line_id)


class BankStatementViewSet(CompanyReadOnlyViewSet):
    queryset = BankStatement.objects.select_related("journal").prefetch_related("lines__matched_line__entry")
    serializer_class = StatementSerializer
    http_method_names = ["get", "post", "head", "options"]
    perms = {"list": "banking.statement.view", "reconciliation": "banking.statement.view",
             "candidates": "banking.statement.view", "*": "banking.statement.manage"}
    filterset_fields = ["journal", "state"]

    def _line(self, request):
        return get_object_or_404(BankStatementLine, pk=request.data.get("line"), statement__company=self.company)

    @action(detail=False, methods=["post"], url_path="import")
    def import_statement(self, request):
        journal = get_object_or_404(Journal, pk=request.data.get("journal"), company=self.company)
        text = request.data.get("csv") or (request.FILES["file"].read().decode("utf-8-sig") if "file" in request.FILES else "")
        st = services.import_csv(self.company, journal, text, request.data.get("opening_balance") or 0,
                                 request.data.get("closing_balance") or 0, request.user,
                                 request.data.get("reference", ""))
        return Response(self.get_serializer(st).data, status=201)

    @action(detail=True, methods=["post"], url_path="auto-match")
    def auto_match(self, request, pk=None):
        n = services.auto_match(self.get_object())
        return Response({"matched": n, "statement": self.get_serializer(self.get_object()).data})

    @action(detail=True, methods=["post"])
    def match(self, request, pk=None):
        jl = get_object_or_404(JournalLine, pk=request.data.get("journal_line"), company=self.company)
        services.match(self._line(request), jl)
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"])
    def unmatch(self, request, pk=None):
        services.unmatch(self._line(request))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="create-entry")
    def create_entry(self, request, pk=None):
        sl = self._line(request)
        acc = get_object_or_404(Account, pk=request.data.get("account"), company=self.company)
        if request.data.get("partner"):
            sl.partner = get_object_or_404(Partner, pk=request.data["partner"], company=self.company)
            sl.save(update_fields=["partner"])
        services.create_entry_for_line(sl, acc, request.user, request.data.get("description", ""))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        return Response(self.get_serializer(services.close_statement(self.get_object())).data)

    @action(detail=True)
    def candidates(self, request, pk=None):
        st = self.get_object()
        lines = (JournalLine.objects.filter(company=self.company, account=st.journal.default_account, posted=True,
                                            bank_match__isnull=True).select_related("entry", "partner")
                 .order_by("-date")[:200])
        return Response([{"id": l.pk, "date": l.date, "number": l.entry.number, "reference": l.entry.reference,
                          "partner": l.partner.name if l.partner else "", "amount": str(l.debit - l.credit),
                          "description": l.description} for l in lines])

    @action(detail=False)
    def reconciliation(self, request):
        journal = get_object_or_404(Journal, pk=request.query_params.get("journal"), company=self.company)
        return Response(services.reconciliation_report(self.company, journal, parse_date(
            request.query_params.get("as_of"), timezone.localdate())))
