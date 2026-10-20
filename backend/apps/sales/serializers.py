from rest_framework import serializers

from apps.core.serializers_base import CompanyScopedSerializer, DocumentSerializer

from .models import SalesOrder, SalesOrderLine


class SalesOrderLineSerializer(CompanyScopedSerializer):
    id = serializers.IntegerField(required=False)
    product_sku = serializers.CharField(source="product.sku", read_only=True)

    class Meta:
        model = SalesOrderLine
        fields = ["id", "product", "product_sku", "description", "quantity", "unit_price", "discount_pct", "tax",
                  "subtotal", "tax_amount", "total", "qty_delivered", "qty_invoiced"]
        read_only_fields = ["subtotal", "tax_amount", "total", "qty_delivered", "qty_invoiced"]


class SalesOrderSerializer(DocumentSerializer):
    line_model = SalesOrderLine
    line_fk = "order"
    lines = SalesOrderLineSerializer(many=True)
    partner_name = serializers.CharField(source="partner.name", read_only=True)

    class Meta:
        model = SalesOrder
        fields = ["id", "number", "partner", "partner_name", "date", "validity_date", "currency", "warehouse",
                  "payment_term", "customer_reference", "state", "amount_untaxed", "amount_tax", "amount_total",
                  "notes", "confirmed_at", "created_at", "lines"]
        read_only_fields = ["number", "state", "amount_untaxed", "amount_tax", "amount_total", "confirmed_at"]

    def after_lines(self, doc):
        from .services import recompute_order
        recompute_order(doc)
