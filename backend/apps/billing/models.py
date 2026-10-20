from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import MONEY, QTY, RATE, CompanyScoped, CostCenter, Currency


class InvoiceType(models.TextChoices):
    OUT_INVOICE = "out_invoice", "Customer invoice"
    OUT_REFUND = "out_refund", "Customer credit note"
    IN_INVOICE = "in_invoice", "Vendor bill"
    IN_REFUND = "in_refund", "Vendor debit note (refund)"


CUSTOMER_TYPES = {InvoiceType.OUT_INVOICE, InvoiceType.OUT_REFUND}
REFUND_TYPES = {InvoiceType.OUT_REFUND, InvoiceType.IN_REFUND}


class DocState(models.TextChoices):
    DRAFT = "draft", "Draft"
    TO_APPROVE = "to_approve", "Waiting approval"
    APPROVED = "approved", "Approved"
    POSTED = "posted", "Posted"
    CANCELLED = "cancelled", "Cancelled"


class PaymentState(models.TextChoices):
    NOT_PAID = "not_paid", "Not paid"
    PARTIAL = "partial", "Partially paid"
    PAID = "paid", "Paid"


class Invoice(CompanyScoped):
    move_type = models.CharField(max_length=12, choices=InvoiceType.choices)
    number = models.CharField(max_length=40, blank=True, db_index=True)
    partner = models.ForeignKey("partners.Partner", on_delete=models.PROTECT, related_name="invoices")
    date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    payment_term = models.ForeignKey("partners.PaymentTerm", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+")
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    rate = models.DecimalField(**RATE, default=Decimal("1"))  # fixed at posting
    reference = models.CharField(max_length=120, blank=True, help_text="Vendor's bill number / customer PO")
    state = models.CharField(max_length=12, choices=DocState.choices, default=DocState.DRAFT, db_index=True)
    payment_state = models.CharField(max_length=10, choices=PaymentState.choices, default=PaymentState.NOT_PAID)
    amount_untaxed = models.DecimalField(**MONEY)
    amount_tax = models.DecimalField(**MONEY)
    amount_total = models.DecimalField(**MONEY)
    amount_total_base = models.DecimalField(**MONEY)
    amount_residual = models.DecimalField(**MONEY)  # still due, document currency
    journal_entry = models.OneToOneField("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                         related_name="invoice")
    origin_invoice = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="refunds")
    sales_order = models.ForeignKey("sales.SalesOrder", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="invoices")
    purchase_order = models.ForeignKey("purchasing.PurchaseOrder", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="bills")
    notes = models.TextField(blank=True)
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], condition=~Q(number=""), name="invoice_number_unique"),
            # same vendor bill number may not be entered twice for one vendor (duplicate payment protection)
            models.UniqueConstraint(fields=["company", "partner", "reference"],
                                    condition=Q(move_type="in_invoice") & ~Q(reference="") & ~Q(state="cancelled"),
                                    name="vendor_bill_ref_unique"),
            models.CheckConstraint(condition=Q(amount_residual__gte=0), name="residual_non_negative"),
        ]

    def __str__(self):
        return self.number or f"Draft {self.get_move_type_display()} #{self.pk}"

    @property
    def is_customer(self):
        return self.move_type in CUSTOMER_TYPES

    @property
    def is_refund(self):
        return self.move_type in REFUND_TYPES


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    sequence = models.PositiveIntegerField(default=0)
    product = models.ForeignKey("inventory.Product", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255)
    account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(**QTY)
    unit_price = models.DecimalField(max_digits=20, decimal_places=6, default=Decimal("0"))
    discount_pct = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal("0"))
    tax = models.ForeignKey("ledger.Tax", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    subtotal = models.DecimalField(**MONEY)   # untaxed, after discount, document currency
    tax_amount = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)
    cost_center = models.ForeignKey(CostCenter, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    sales_line = models.ForeignKey("sales.SalesOrderLine", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="invoice_lines")
    purchase_line = models.ForeignKey("purchasing.PurchaseOrderLine", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="bill_lines")

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="invoice_line_qty_positive"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="invoice_line_price_non_negative"),
            models.CheckConstraint(condition=Q(discount_pct__gte=0) & Q(discount_pct__lte=100),
                                   name="invoice_line_discount_range"),
        ]


class Payment(CompanyScoped):
    class Direction(models.TextChoices):
        INBOUND = "inbound", "Receipt from customer"
        OUTBOUND = "outbound", "Payment to vendor"

    direction = models.CharField(max_length=10, choices=Direction.choices)
    number = models.CharField(max_length=40, blank=True, db_index=True)
    partner = models.ForeignKey("partners.Partner", on_delete=models.PROTECT, related_name="payments")
    journal = models.ForeignKey("ledger.Journal", on_delete=models.PROTECT, related_name="+")
    date = models.DateField()
    amount = models.DecimalField(**MONEY)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    rate = models.DecimalField(**RATE, default=Decimal("1"))
    reference = models.CharField(max_length=120, blank=True, help_text="Cheque / transfer reference")
    memo = models.CharField(max_length=255, blank=True)
    state = models.CharField(max_length=12, choices=DocState.choices, default=DocState.DRAFT, db_index=True)
    amount_unallocated = models.DecimalField(**MONEY)
    journal_entry = models.OneToOneField("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                         related_name="payment")
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], condition=~Q(number=""), name="payment_number_unique"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
            models.CheckConstraint(condition=Q(amount_unallocated__gte=0), name="payment_unallocated_non_negative"),
        ]

    def __str__(self):
        return self.number or f"Draft payment #{self.pk}"


class Allocation(models.Model):
    """Settles an invoice with a payment or with a credit note. Amount is in invoice currency."""
    company = models.ForeignKey("core.Company", on_delete=models.PROTECT, related_name="+")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="allocations")
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.PROTECT, related_name="allocations")
    credit_note = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="credit_allocations")
    amount = models.DecimalField(**MONEY)
    date = models.DateField()
    fx_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="allocation_positive"),
            models.CheckConstraint(
                condition=(Q(payment__isnull=False) & Q(credit_note__isnull=True))
                | (Q(payment__isnull=True) & Q(credit_note__isnull=False)),
                name="allocation_one_source"),
        ]
