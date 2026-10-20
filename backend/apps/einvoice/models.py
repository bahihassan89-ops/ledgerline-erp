"""Egyptian Tax Authority (ETA) e-invoicing."""
from decimal import Decimal

from django.db import models

from apps.core.crypto import EncryptedTextField
from apps.core.signals import audited

HOSTS = {
    "preprod": {"id": "https://id.preprod.eta.gov.eg", "api": "https://api.preprod.invoicing.eta.gov.eg",
                "portal": "https://preprod.invoicing.eta.gov.eg"},
    "prod": {"id": "https://id.eta.gov.eg", "api": "https://api.invoicing.eta.gov.eg",
             "portal": "https://invoicing.eta.gov.eg"},
}


@audited
class EtaSettings(models.Model):
    company = models.OneToOneField("core.Company", on_delete=models.CASCADE, related_name="eta")
    enabled = models.BooleanField(default=False)
    environment = models.CharField(max_length=8, default="preprod", choices=[("preprod", "Pre-production (testing)"),
                                                                             ("prod", "Production")])
    client_id = models.CharField(max_length=100, blank=True)
    client_secret = EncryptedTextField(blank=True, default="")
    taxpayer_activity_code = models.CharField(max_length=10, blank=True, help_text="ETA activity code (ISIC based)")
    branch_id = models.CharField(max_length=20, default="0")
    governate = models.CharField(max_length=60, blank=True)
    region_city = models.CharField(max_length=60, blank=True)
    street = models.CharField(max_length=200, blank=True)
    building_number = models.CharField(max_length=30, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    floor = models.CharField(max_length=20, blank=True)
    room = models.CharField(max_length=20, blank=True)
    landmark = models.CharField(max_length=120, blank=True)
    additional_information = models.CharField(max_length=200, blank=True)
    # Signing: the USB token (eSeal) is driven by a signing agent on a trusted machine; we call it over HTTPS.
    signer_mode = models.CharField(max_length=20, default="service", choices=[
        ("service", "Signing agent (USB token / HSM)"), ("unsigned_preprod", "Unsigned v0.9 (pre-production only)")])
    signer_url = models.URLField(blank=True)
    signer_token = EncryptedTextField(blank=True, default="")
    auto_submit = models.BooleanField(default=True, help_text="Submit automatically when an invoice is posted")
    # Thresholds that change by decree: verify with your tax advisor.
    person_id_threshold = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("25000"),
                                              help_text="Individuals: national ID required at/above this total (EGP)")
    max_submission_days = models.PositiveSmallIntegerField(default=7, help_text="Warn when submission is later than this")

    def hosts(self):
        return HOSTS[self.environment]


class EtaDocState(models.TextChoices):
    PENDING = "pending", "Waiting to submit"
    SUBMITTED = "submitted", "Submitted (validating)"
    VALID = "valid", "Valid"
    INVALID = "invalid", "Invalid"
    REJECTED = "rejected", "Rejected"
    CANCEL_REQUESTED = "cancel_requested", "Cancellation requested"
    CANCELLED = "cancelled", "Cancelled"
    FAILED = "failed", "Failed (technical)"


class EtaDocument(models.Model):
    company = models.ForeignKey("core.Company", on_delete=models.PROTECT, related_name="+")
    invoice = models.OneToOneField("billing.Invoice", on_delete=models.PROTECT, related_name="eta_document")
    document_type = models.CharField(max_length=1)  # I / C / D
    internal_id = models.CharField(max_length=60)
    state = models.CharField(max_length=20, choices=EtaDocState.choices, default=EtaDocState.PENDING, db_index=True)
    payload = models.JSONField(default=dict)
    submission_uuid = models.CharField(max_length=60, blank=True)
    uuid = models.CharField(max_length=80, blank=True, db_index=True)
    long_id = models.CharField(max_length=200, blank=True)
    hash_key = models.CharField(max_length=200, blank=True)
    errors = models.JSONField(default=list, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def public_url(self):
        if not (self.uuid and self.long_id):
            return ""
        portal = EtaSettings.objects.get(company=self.company).hosts()["portal"]
        return f"{portal}/print/documents/{self.uuid}/share/{self.long_id}"


class EtaReceivedDocument(models.Model):
    """Documents vendors issued to us on the ETA portal; matched against vendor bills."""
    company = models.ForeignKey("core.Company", on_delete=models.PROTECT, related_name="+")
    uuid = models.CharField(max_length=80)
    internal_id = models.CharField(max_length=60, blank=True)
    document_type = models.CharField(max_length=2, blank=True)
    issuer_id = models.CharField(max_length=30, blank=True)
    issuer_name = models.CharField(max_length=200, blank=True)
    date_issued = models.DateTimeField(null=True, blank=True)
    total = models.DecimalField(max_digits=20, decimal_places=5, default=Decimal("0"))
    status = models.CharField(max_length=20, blank=True)
    bill = models.ForeignKey("billing.Invoice", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("company", "uuid")]
        ordering = ["-date_issued"]
