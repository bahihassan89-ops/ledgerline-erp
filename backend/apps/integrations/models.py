import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.crypto import EncryptedTextField
from apps.core.models import CompanyScoped
from apps.core.signals import audited

WEBHOOK_EVENTS = [
    "invoice.posted", "invoice.paid", "bill.posted", "bill.paid", "payment.posted",
    "journal_entry.posted", "journal_entry.reversed", "sales_order.confirmed", "purchase_order.confirmed",
    "stock.moved", "partner.created", "partner.updated", "product.created", "product.updated",
]


@audited
class ApiKey(CompanyScoped):
    """Integration credential. Only a SHA-256 hash of the secret is stored; the full key is shown once.
    Effective rights = owner's rights in the company ∩ scopes."""
    name = models.CharField(max_length=120)
    prefix = models.CharField(max_length=12, unique=True, editable=False)
    key_hash = models.CharField(max_length=64, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="api_keys")
    scopes = models.JSONField(default=list)
    allowed_ips = models.JSONField(default=list, blank=True, help_text="Empty = any IP")
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.prefix})"

    @staticmethod
    def hash_secret(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()

    @classmethod
    def generate(cls, **kwargs):
        prefix = secrets.token_hex(4)
        secret = secrets.token_urlsafe(32)
        obj = cls(prefix=prefix, key_hash=cls.hash_secret(secret), **kwargs)
        obj.save()
        return obj, f"{prefix}.{secret}"

    def is_valid(self):
        return self.active and (self.expires_at is None or self.expires_at > timezone.now())


@audited
class Webhook(CompanyScoped):
    name = models.CharField(max_length=120)
    url = models.URLField(max_length=500)
    events = models.JSONField(default=list)
    secret = EncryptedTextField(editable=False)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.secret:
            self.secret = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)


class WebhookDelivery(models.Model):
    """Outbox: rows are written in the same transaction as the business event, then delivered
    by the `deliver_webhooks` worker with retries and HMAC-SHA256 signatures."""
    webhook = models.ForeignKey(Webhook, on_delete=models.CASCADE, related_name="deliveries")
    event = models.CharField(max_length=60)
    event_id = models.CharField(max_length=40, unique=True)
    payload = models.JSONField()
    status = models.CharField(max_length=10, default="pending", db_index=True)  # pending / sent / failed
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class IdempotencyRecord(models.Model):
    """Lets integrations safely retry POSTs with an `Idempotency-Key` header."""
    company = models.ForeignKey("core.Company", on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    key = models.CharField(max_length=100)
    path = models.CharField(max_length=255)
    status_code = models.PositiveSmallIntegerField()
    body = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("company", "user", "key")]
