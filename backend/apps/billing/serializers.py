from rest_framework import serializers

from apps.core.serializers_base import CompanyScopedSerializer, DocumentSerializer

from . import services
from .models import Allocation, Invoice, InvoiceLine, Payment


class InvoiceLineSerializer(CompanyScopedSerializer):
    id = serializers.IntegerField(required=False)
    account_code = serializers.CharField(source="account.code", read_only=True)

    class Meta:
        model = InvoiceLine
        fields = ["id", "product", "description", "account", "account_code", "quantity", "unit_price",
                  "discount_pct", "tax", "subtotal", "tax_amount", "total", "cost_center", "sales_line",
                  "purchase_line"]
        read_only_fields = ["subtotal", "tax_amount", "total", "sales_line", "purchase_line"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        acc = attrs.get("account")
        if acc and (acc.is_group or not acc.active):
            raise serializers.ValidationError({"account": "Choose an active postable account."})
        if acc and acc.reconcilable:
            raise serializers.ValidationError({"account": "Invoice lines cannot post to receivable/payable accounts."})
        return attrs


class AllocationSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    payment_number = serializers.CharField(source="payment.number", read_only=True, default=None)
    credit_note_number = serializers.CharField(source="credit_note.number", read_only=True, default=None)
    fx_entry_number = serializers.CharField(source="fx_entry.number", read_only=True, default=None)

    class Meta:
        model = Allocation
        fields = ["id", "invoice", "invoice_number", "payment", "payment_number", "credit_note",
                  "credit_note_number", "amount", "date", "fx_entry", "fx_entry_number", "created_at"]


class InvoiceSerializer(DocumentSerializer):
    line_model = InvoiceLine
    line_fk = "invoice"
    lines = InvoiceLineSerializer(many=True)
    partner_name = serializers.CharField(source="partner.name", read_only=True)
    entry_number = serializers.CharField(source="journal_entry.number", read_only=True, default=None)
    origin_number = serializers.CharField(source="origin_invoice.number", read_only=True, default=None)
    allocations = serializers.SerializerMethodField()
    days_overdue = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = ["id", "move_type", "number", "partner", "partner_name", "date", "due_date", "payment_term",
                  "currency", "rate", "reference", "state", "payment_state", "amount_untaxed", "amount_tax",
                  "amount_total", "amount_total_base", "amount_residual", "journal_entry", "entry_number",
                  "origin_invoice", "origin_number", "sales_order", "purchase_order", "notes", "posted_at",
                  "created_at", "lines", "allocations", "days_overdue"]
        read_only_fields = ["number", "state", "payment_state", "amount_untaxed", "amount_tax", "amount_total",
                            "amount_total_base", "amount_residual", "journal_entry", "origin_invoice",
                            "sales_order", "purchase_order", "posted_at"]
        extra_kwargs = {"rate": {"required": False}}

    def get_allocations(self, obj):
        qs = list(obj.allocations.select_related("payment", "credit_note", "fx_entry")) + \
            list(obj.credit_allocations.select_related("invoice", "fx_entry"))
        return AllocationSerializer(qs, many=True).data

    def get_days_overdue(self, obj):
        from django.utils import timezone
        if obj.state != "posted" or not obj.amount_residual or not obj.due_date:
            return 0
        return max((timezone.localdate() - obj.due_date).days, 0)

    def validate_move_type(self, value):
        allowed = self.context.get("allowed_types")
        if allowed and value not in allowed:
            raise serializers.ValidationError(f"Allowed here: {', '.join(allowed)}")
        if self.instance and value != self.instance.move_type:
            raise serializers.ValidationError("Document type cannot change.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance is None and not attrs.get("lines"):
            raise serializers.ValidationError({"lines": "At least one line is required."})
        return attrs

    def after_lines(self, doc):
        services.recompute(doc)


class PaymentSerializer(CompanyScopedSerializer):
    partner_name = serializers.CharField(source="partner.name", read_only=True)
    journal_code = serializers.CharField(source="journal.code", read_only=True)
    entry_number = serializers.CharField(source="journal_entry.number", read_only=True, default=None)
    allocations = AllocationSerializer(many=True, read_only=True)

    class Meta:
        model = Payment
        fields = ["id", "direction", "number", "partner", "partner_name", "journal", "journal_code", "date",
                  "amount", "currency", "rate", "reference", "memo", "state", "amount_unallocated", "journal_entry",
                  "entry_number", "posted_at", "allocations"]
        read_only_fields = ["number", "state", "amount_unallocated", "journal_entry", "posted_at"]
        extra_kwargs = {"rate": {"required": False}}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance and self.instance.state != "draft":
            raise serializers.ValidationError("Only draft payments can be edited.")
        if attrs.get("amount") is not None and attrs["amount"] <= 0:
            raise serializers.ValidationError({"amount": "Amount must be positive."})
        j = attrs.get("journal")
        if j and j.type not in ("bank", "cash"):
            raise serializers.ValidationError({"journal": "Choose a bank or cash journal."})
        return attrs
