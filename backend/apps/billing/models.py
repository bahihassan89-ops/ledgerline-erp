from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import MONEY, QTY, RATE, CompanyScoped, CostCenter, Currency
from apps.core.signals import audited


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
    amount_wht = models.DecimalField(**MONEY)  # withholding tax deducted (Egypt WHT)
    amount_total = models.DecimalField(**MONEY)  # = untaxed + VAT - WHT (what the partner pays / is paid)
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
    # Credit/debit notes for goods physically returned: stock moves are created when posted.
    return_to_stock = models.BooleanField(default=False)
    # Imported open item from the previous system (go-live). Not e-invoiced, no 3-way match.
    is_opening = models.BooleanField(default=False)
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
    withholding_tax = models.ForeignKey("ledger.Tax", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+")
    subtotal = models.DecimalField(**MONEY)   # untaxed, after discount, document currency
    tax_amount = models.DecimalField(**MONEY)
    wht_amount = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)
    cost_center = models.ForeignKey(CostCenter, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    sales_line = models.ForeignKey("sales.SalesOrderLine", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="invoice_lines")
    purchase_line = models.ForeignKey("purchasing.PurchaseOrderLine", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="bill_lines")
    mo_operation = models.ForeignKey("manufacturing.MoOperation", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="subcontract_bill_lines",
                                     help_text="Set when this line bills a subcontractor for an operation's "
                                               "charge -- see manufacturing.services.create_subcontract_bill")

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
    # Cheques (أوراق قبض / أوراق دفع): posted to the cheques account until the bank clears them.
    method = models.CharField(max_length=10, default="transfer", choices=[
        ("transfer", "Bank transfer"), ("cash", "Cash"), ("cheque", "Cheque")])
    cheque_number = models.CharField(max_length=40, blank=True)
    cheque_bank = models.CharField(max_length=120, blank=True, help_text="Drawee bank")
    cheque_due_date = models.DateField(null=True, blank=True)
    cheque_state = models.CharField(max_length=12, blank=True, choices=[
        ("in_hand", "In hand (received)"), ("deposited", "Deposited for collection"), ("issued", "Issued to vendor"),
        ("cleared", "Cleared"), ("endorsed", "Endorsed to third party"), ("bounced", "Bounced / cancelled")])
    cheque_cleared_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                             related_name="+")
    # Endorsement (FC review): a received cheque settled directly against one of our own vendors
    # instead of being deposited -- see billing.services.cheque_endorse.
    cheque_endorsed_to = models.ForeignKey("partners.Partner", null=True, blank=True, on_delete=models.PROTECT,
                                           related_name="+")
    cheque_endorsed_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                              on_delete=models.PROTECT, related_name="+")
    # Set when this cheque was auto-generated by a ChequeInstalmentPlan (post-dated instalment
    # schedule, e.g. real estate unit sales/purchases) -- see billing.services.generate_instalment_cheques.
    instalment_plan = models.ForeignKey("ChequeInstalmentPlan", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="cheques")
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


@audited
class ChequeInstalmentPlan(CompanyScoped):
    """A schedule of equal post-dated cheques covering one contract/invoice in instalments -- the
    pattern our real estate companies use for unit sales and purchases (FC review, Oct 2026).
    Generating the plan creates and posts one cheque `Payment` per instalment, exactly as if all
    the physical post-dated cheques were received/issued and logged at signing; each one is then
    deposited/cleared/bounced/endorsed individually through the normal cheque workflow as its own
    due date arrives -- see billing.services.generate_instalment_cheques."""
    direction = models.CharField(max_length=10, choices=[
        ("inbound", "Receipt from customer"), ("outbound", "Payment to vendor")])
    partner = models.ForeignKey("partners.Partner", on_delete=models.PROTECT, related_name="cheque_plans")
    journal = models.ForeignKey("ledger.Journal", on_delete=models.PROTECT, related_name="+")
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.PROTECT, related_name="cheque_plans")
    description = models.CharField(max_length=200, blank=True)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    total_amount = models.DecimalField(**MONEY)
    first_due_date = models.DateField()
    number_of_instalments = models.PositiveSmallIntegerField()
    frequency_months = models.PositiveSmallIntegerField(default=1)
    state = models.CharField(max_length=10, default="draft", choices=[
        ("draft", "Draft"), ("generated", "Generated")])

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.description or f"Instalment plan #{self.pk}"


@audited
class GuaranteeCheque(CompanyScoped):
    """A cheque held purely as security/collateral -- e.g. a tender or performance guarantee --
    never posted to the GL, so it stays off balance sheet until it is returned or forfeited, per
    the FC review ("guarantee cheques held off balance sheet"). If one is forfeited, the money is
    actually collected or paid; book that separately as a normal cheque/payment (or manual journal)
    -- this record only tracks custody, due date and status, it never creates a journal entry."""
    class Direction(models.TextChoices):
        HELD_FROM_CUSTOMER = "held_from_customer", "Held from customer (collateral we hold)"
        GIVEN_TO_SUPPLIER = "given_to_supplier", "Given to supplier (collateral we posted)"

    direction = models.CharField(max_length=20, choices=Direction.choices)
    partner = models.ForeignKey("partners.Partner", on_delete=models.PROTECT, related_name="guarantee_cheques")
    cheque_number = models.CharField(max_length=40)
    bank = models.CharField(max_length=120, blank=True, help_text="Drawee bank")
    amount = models.DecimalField(**MONEY)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    due_date = models.DateField(null=True, blank=True, help_text="Validity / expiry date, if any")
    reason = models.CharField(max_length=200, blank=True,
                              help_text="e.g. tender/performance guarantee for contract X")
    received_date = models.DateField()
    state = models.CharField(max_length=10, default="held", choices=[
        ("held", "Held"), ("returned", "Returned"), ("forfeited", "Forfeited")])
    returned_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-received_date", "-id"]

    def __str__(self):
        return f"Guarantee cheque {self.cheque_number} ({self.partner})"
