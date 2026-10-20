from rest_framework import serializers

from apps.core.serializers_base import CompanyScopedSerializer, DocumentSerializer

from .models import PurchaseOrder, PurchaseOrderLine


class PurchaseOrderLineSerializer(CompanyScopedSerializer):
    id = serializers.IntegerField(required=False)
    product_sku = serializers.CharField(source="product.sku", read_only=True)

    class Meta:
        model = PurchaseOrderLine
        fields = ["id", "product", "product_sku", "description", "quantity", "unit_price", "discount_pct", "tax",
                  "subtotal", "tax_amount", "total", "qty_received", "qty_billed"]
        read_only_fields = ["subtotal", "tax_amount", "total", "qty_received", "qty_billed"]


class PurchaseOrderSerializer(DocumentSerializer):
    line_model = PurchaseOrderLine
    line_fk = "order"
    lines = PurchaseOrderLineSerializer(many=True)
    partner_name = serializers.CharField(source="partner.name", read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = ["id", "number", "partner", "partner_name", "date", "expected_date", "currency", "warehouse",
                  "payment_term", "vendor_reference", "state", "amount_untaxed", "amount_tax", "amount_total",
                  "notes", "confirmed_at", "created_at", "lines"]
        read_only_fields = ["number", "state", "amount_untaxed", "amount_tax", "amount_total", "confirmed_at"]

    def after_lines(self, doc):
        from .services import recompute_order
        recompute_order(doc)
