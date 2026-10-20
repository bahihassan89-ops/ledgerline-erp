from django.conf import settings
from django.db import models

from apps.core.models import CompanyScoped
from apps.core.signals import audited


class DocumentType(models.TextChoices):
    SALES_ORDER = "sales_order", "Sales order"
    PURCHASE_ORDER = "purchase_order", "Purchase order"
    CUSTOMER_INVOICE = "customer_invoice", "Customer invoice / credit note"
    VENDOR_BILL = "vendor_bill", "Vendor bill / debit note"
    PAYMENT = "payment", "Payment"
    JOURNAL_ENTRY = "journal_entry", "Manual journal entry"


@audited
class ApprovalRule(CompanyScoped):
    """Documents of `document_type` whose base-currency amount is >= `min_amount` need approval
    by each level in `approver_levels`, in order. The rule with the highest matching min_amount wins."""
    name = models.CharField(max_length=120)
    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    min_amount = models.DecimalField(max_digits=20, decimal_places=2)
    approver_levels = models.JSONField(default=list, help_text="e.g. [3, 4] = Senior Accountant then Controller")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["document_type", "min_amount"]

    def __str__(self):
        return self.name


class ApprovalRequest(CompanyScoped):
    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    document_id = models.BigIntegerField()
    document_label = models.CharField(max_length=120)
    amount = models.DecimalField(max_digits=20, decimal_places=4)  # base currency
    rule = models.ForeignKey(ApprovalRule, on_delete=models.PROTECT, related_name="+")
    levels = models.JSONField(default=list)
    current_step = models.PositiveSmallIntegerField(default=0)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING, db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["document_type", "document_id"])]

    @property
    def required_level(self):
        return self.levels[self.current_step] if self.current_step < len(self.levels) else None


class ApprovalDecision(models.Model):
    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="decisions")
    step = models.PositiveSmallIntegerField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    approved = models.BooleanField()
    comment = models.TextField(blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at"]
