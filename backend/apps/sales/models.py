from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import MONEY, QTY, CompanyScoped, Currency


class OrderState(models.TextChoices):
    DRAFT = "draft", "Quotation"
    TO_APPROVE = "to_approve", "Waiting approval"
    APPROVED = "approved", "Approved"
    CONFIRMED = "confirmed", "Confirmed"
    DONE = "done", "Fully delivered & invoiced"
    CANCELLED = "cancelled", "Cancelled"


class SalesOrder(CompanyScoped):
    number = models.CharField(max_length=40, blank=True, db_index=True)
    partner = models.ForeignKey("partners.Partner", on_delete=models.PROTECT, related_name="sales_orders")
    date = models.DateField()
    validity_date = models.DateField(null=True, blank=True)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")
    payment_term = models.ForeignKey("partners.PaymentTerm", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+")
    customer_reference = models.CharField(max_length=120, blank=True)
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
        return self.number or f"Quotation #{self.pk}"


class SalesOrderLine(models.Model):
    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="lines")
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
    qty_delivered = models.DecimalField(**QTY)
    qty_invoiced = models.DecimalField(**QTY)

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="so_line_qty_positive"),
            models.CheckConstraint(condition=Q(qty_delivered__lte=models.F("quantity")), name="so_line_no_overdelivery"),
        ]
