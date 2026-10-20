from django.db.models import Sum
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import CompanyViewSet
from apps.core.exceptions import BusinessError
from apps.integrations.events import emit
from apps.ledger.models import JournalLine
from apps.ledger.views import parse_date

from .models import Partner, PaymentTerm
from .serializers import PartnerSerializer, PaymentTermSerializer


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
        emit(self.company, "partner.created", PartnerSerializer(p).data)

    def perform_update(self, serializer):
        p = serializer.save()
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
