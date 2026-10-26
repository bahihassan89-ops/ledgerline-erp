from django.db.models import Case, DecimalField, F, Sum, Value, When
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import CompanyViewSet
from apps.core.exceptions import BusinessError
from apps.core.money import D
from apps.iam.access import require
from apps.ledger.models import Journal
from apps.ledger.views import parse_date

from . import services
from .models import Allocation, DocState, Invoice, InvoiceType, Payment, REFUND_TYPES
from .serializers import InvoiceSerializer, PaymentSerializer


class _InvoiceBase(CompanyViewSet):
    serializer_class = InvoiceSerializer
    types = ()
    p = ""  # permission prefix
    filterset_fields = {"state": ["exact"], "payment_state": ["exact"], "partner": ["exact"],
                        "move_type": ["exact"], "date": ["gte", "lte"], "due_date": ["lte"]}
    search_fields = ["number", "reference", "partner__name"]
    ordering_fields = ["date", "due_date", "number", "amount_total", "amount_residual"]

    def get_queryset(self):
        return (Invoice.objects.filter(company=self.company, move_type__in=self.types)
                .select_related("partner", "journal_entry", "origin_invoice").prefetch_related("lines"))

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "allowed_types": self.types}

    def perform_destroy(self, instance):
        if instance.state != DocState.DRAFT:
            raise BusinessError("Only drafts can be deleted; cancel posted documents instead.", "not_draft")
        instance.delete()

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        inv = services.post_invoice(self.get_object(), request.user)
        return Response(self.get_serializer(inv).data, status=202 if inv.state == DocState.TO_APPROVE else 200)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        inv = services.cancel_invoice(self.get_object(), request.user, request.data.get("reason", ""))
        return Response(self.get_serializer(inv).data)

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        ref = services.create_refund(self.get_object(), request.user, reason=request.data.get("reason", ""),
                                     return_to_stock=bool(request.data.get("return_to_stock")))
        return Response(self.get_serializer(ref).data, status=201)

    @action(detail=True, methods=["post"], url_path="debit-note")
    def debit_note(self, request, pk=None):
        """Additional charge to the customer referencing a posted invoice (ETA document type D)."""
        origin = self.get_object()
        if origin.move_type != InvoiceType.OUT_INVOICE or origin.state != DocState.POSTED:
            raise BusinessError("Debit notes reference a posted customer invoice.", "invalid_state")
        dn = Invoice.objects.create(company=self.company, move_type=InvoiceType.OUT_INVOICE, partner=origin.partner,
                                    date=timezone.localdate(), currency=origin.currency, origin_invoice=origin,
                                    reference=f"Debit note to {origin.number}", created_by=request.user,
                                    notes=request.data.get("reason", ""))
        return Response(self.get_serializer(dn).data, status=201)

    @action(detail=False, url_path="open-balance")
    def open_balance(self, request):
        """Sum of amount_residual (in base currency) across every posted document matching the
        current filters — computed in the DB so it isn't capped by the list endpoint's page size."""
        qs = self.filter_queryset(self.get_queryset()).filter(state=DocState.POSTED)
        total = qs.aggregate(total=Coalesce(Sum(
            Case(When(move_type__in=REFUND_TYPES, then=-F("amount_residual") * F("rate")),
                 default=F("amount_residual") * F("rate"),
                 output_field=DecimalField(max_digits=20, decimal_places=4)),
        ), Value(D(0), output_field=DecimalField(max_digits=20, decimal_places=4))))["total"]
        return Response({"total": str(total)})

    @action(detail=True, methods=["post"], url_path="register-payment")
    def register_payment(self, request, pk=None):
        """Create, post and allocate a payment for this document in one step."""
        require(request, "billing.payment.post")
        inv = self.get_object()
        if inv.state != DocState.POSTED or not inv.amount_residual:
            raise BusinessError("Document is not open for payment.", "not_open")
        journal = get_object_or_404(Journal, pk=request.data.get("journal"), company=self.company)
        amount = D(request.data.get("amount") or inv.amount_residual)
        receiving = inv.move_type in (InvoiceType.OUT_INVOICE, InvoiceType.IN_REFUND)
        pay = Payment.objects.create(
            company=self.company, direction="inbound" if receiving else "outbound", partner=inv.partner,
            journal=journal, date=parse_date(request.data.get("date"), timezone.localdate()), amount=amount,
            currency=inv.currency, rate=D(request.data.get("rate") or 1), reference=request.data.get("reference", ""),
            memo=f"Payment of {inv.number}", created_by=request.user, method=request.data.get("method", "transfer"),
            cheque_number=request.data.get("cheque_number", ""), cheque_bank=request.data.get("cheque_bank", ""),
            cheque_due_date=parse_date(request.data.get("cheque_due_date")))
        pay = services.post_payment(pay, request.user, allocations=[(inv, min(amount, inv.amount_residual))])
        inv.refresh_from_db()
        return Response({"payment": PaymentSerializer(pay).data, "invoice": self.get_serializer(inv).data})


class CustomerInvoiceViewSet(_InvoiceBase):
    types = (InvoiceType.OUT_INVOICE, InvoiceType.OUT_REFUND)
    perms = {"list": "billing.invoice.view", "create": "billing.invoice.create", "update": "billing.invoice.create",
             "destroy": "billing.invoice.create", "post": "billing.invoice.post", "cancel": "billing.invoice.cancel",
             "refund": "billing.invoice.cancel", "register_payment": "billing.payment.post",
             "debit_note": "billing.invoice.create", "open_balance": "billing.invoice.view"}


class VendorBillViewSet(_InvoiceBase):
    types = (InvoiceType.IN_INVOICE, InvoiceType.IN_REFUND)
    perms = {"list": "billing.bill.view", "create": "billing.bill.create", "update": "billing.bill.create",
             "destroy": "billing.bill.create", "post": "billing.bill.post", "cancel": "billing.bill.cancel",
             "refund": "billing.bill.cancel", "register_payment": "billing.payment.post",
             "open_balance": "billing.bill.view"}


class AllocateSerializer(serializers.Serializer):
    invoice = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=20, decimal_places=4)
    date = serializers.DateField(required=False)


class PaymentViewSet(CompanyViewSet):
    queryset = Payment.objects.select_related("partner", "journal", "journal_entry").prefetch_related("allocations")
    serializer_class = PaymentSerializer
    perms = {"list": "billing.payment.view", "create": "billing.payment.create", "update": "billing.payment.create",
             "destroy": "billing.payment.create", "post": "billing.payment.post", "cancel": "billing.payment.post",
             "allocate": "billing.payment.post", "unallocate": "billing.payment.post",
             "cheque_deposit": "billing.cheque.manage", "cheque_clear": "billing.cheque.manage",
             "cheque_bounce": "billing.cheque.manage", "cheques": "billing.payment.view"}
    filterset_fields = {"state": ["exact"], "direction": ["exact"], "partner": ["exact"], "date": ["gte", "lte"]}
    search_fields = ["number", "reference", "partner__name"]

    def perform_destroy(self, instance):
        if instance.state != DocState.DRAFT:
            raise BusinessError("Only drafts can be deleted.", "not_draft")
        instance.delete()

    def _allocs(self, data):
        out = []
        for a in data or []:
            s = AllocateSerializer(data=a)
            s.is_valid(raise_exception=True)
            inv = get_object_or_404(Invoice, pk=s.validated_data["invoice"], company=self.company)
            out.append((inv, s.validated_data["amount"]))
        return out

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        pay = services.post_payment(self.get_object(), request.user, self._allocs(request.data.get("allocations")))
        return Response(self.get_serializer(pay).data)

    @action(detail=True, methods=["post"])
    def allocate(self, request, pk=None):
        pay = self.get_object()
        for inv, amount in self._allocs(request.data.get("allocations") or [request.data]):
            services.allocate(self.company, inv, amount, request.user, payment=pay,
                              on_date=parse_date(request.data.get("date"), timezone.localdate()))
        pay.refresh_from_db()
        return Response(self.get_serializer(pay).data)

    @action(detail=True, methods=["post"])
    def unallocate(self, request, pk=None):
        pay = self.get_object()
        alloc = get_object_or_404(Allocation, pk=request.data.get("allocation"), payment=pay)
        services.remove_allocation(alloc, request.user)
        pay.refresh_from_db()
        return Response(self.get_serializer(pay).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        pay = services.cancel_payment(self.get_object(), request.user, request.data.get("reason", ""))
        return Response(self.get_serializer(pay).data)

    @action(detail=True, methods=["post"], url_path="cheque-deposit")
    def cheque_deposit(self, request, pk=None):
        return Response(self.get_serializer(services.cheque_deposit(self.get_object(), request.user)).data)

    @action(detail=True, methods=["post"], url_path="cheque-clear")
    def cheque_clear(self, request, pk=None):
        pay = services.cheque_clear(self.get_object(), request.user,
                                    parse_date(request.data.get("date"), timezone.localdate()))
        return Response(self.get_serializer(pay).data)

    @action(detail=True, methods=["post"], url_path="cheque-bounce")
    def cheque_bounce(self, request, pk=None):
        pay = services.cheque_bounce(self.get_object(), request.user, request.data.get("reason", ""))
        return Response(self.get_serializer(pay).data)

    @action(detail=False)
    def cheques(self, request):
        return Response(services.cheque_register(self.company, request.query_params.get("direction"),
                                                 request.query_params.get("state")))
