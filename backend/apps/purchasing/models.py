from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import MONEY, QTY, CompanyScoped, Currency
from apps.sales.models import OrderState


class PurchaseOrder(CompanyScoped):
    number = models.CharField(max_length=40, blank=True, db_index=True)
    partner = models.ForeignKey("partners.Partner", on_delete=models.PROTECT, related_name="purchase_orders")
    date = models.DateField()
    expected_date = models.DateField(null=True, blank=True)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")
    payment_term = models.ForeignKey("partners.PaymentTerm", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+")
    vendor_reference = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=12, choices=OrderState.choices, default=OrderState.DRAFT, db_index=True)
    amount_untaxed = models.DecimalField(**MONEY)
    amount_tax = models.DecimalField(**MONEY)
    amount_total = models.DecimalField(**MONEY)
    notes = models.TextField(blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return self.number or f"RFQ #{self.pk}"


class PurchaseOrderLine(models.Model):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    sequence = models.PositiveIntegerField(default=0)
    product = models.ForeignKey("inventory.Product", on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(**QTY)
    unit_price = models.DecimalField(max_digits=20, decimal_places=6, default=Decimal("0"))
    discount_pct = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal("0"))
    tax = models.ForeignKey("ledger.Tax", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    subtotal = models.DecimalField(**MONEY)
    tax_amount = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)
    qty_received = models.DecimalField(**QTY)
    qty_billed = models.DecimalField(**QTY)
    # Base-currency value of what was received (for GRNI clearing at bill time)
    received_value = models.DecimalField(**MONEY)
    # Base-currency GRNI already cleared by posted bills
    grni_cleared = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="po_line_qty_positive"),
            models.CheckConstraint(condition=Q(qty_received__lte=models.F("quantity")), name="po_line_no_overreceipt"),
        ]
