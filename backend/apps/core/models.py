import hashlib
import json
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, models, transaction
from django.utils import timezone

MONEY = dict(max_digits=20, decimal_places=4, default=Decimal("0"))
QTY = dict(max_digits=18, decimal_places=4, default=Decimal("0"))
RATE = dict(max_digits=20, decimal_places=10)


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )

    class Meta:
        abstract = True


class Currency(models.Model):
    code = models.CharField(max_length=3, primary_key=True)  # ISO 4217
    name = models.CharField(max_length=64)
    symbol = models.CharField(max_length=8, blank=True)
    decimals = models.PositiveSmallIntegerField(default=2)
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "currencies"
        ordering = ["code"]

    def __str__(self):
        return self.code

    def round(self, value) -> Decimal:
        from .money import round_to
        return round_to(value, self.decimals)


class Company(TimeStamped):
    name = models.CharField(max_length=200)
    name_ar = models.CharField(max_length=200, blank=True)
    legal_name = models.CharField(max_length=255, blank=True)
    code = models.SlugField(max_length=20, unique=True)
    tax_number = models.CharField(max_length=50, blank=True, help_text="VAT / TRN / tax registration number")
    registration_number = models.CharField(max_length=50, blank=True, help_text="Commercial registry (السجل التجاري)")
    tax_office = models.CharField(max_length=120, blank=True, help_text="مأمورية الضرائب")
    base_currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    country = models.CharField(max_length=2, blank=True)
    address = models.TextField(blank=True)
    fiscal_year_start_month = models.PositiveSmallIntegerField(default=1)
    # No posting on or before this date by anyone (hard lock).
    lock_date = models.DateField(null=True, blank=True)
    # Price tolerance for 3-way match (percent above PO price allowed on a bill).
    bill_price_tolerance_pct = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "companies"
        ordering = ["name"]

    def __str__(self):
        return self.name


class CompanyScoped(TimeStamped):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")

    class Meta:
        abstract = True


class ExchangeRate(CompanyScoped):
    """Rate expressed as: 1 unit of `currency` = `rate` units of company base currency."""
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    date = models.DateField()
    rate = models.DecimalField(**RATE)

    class Meta:
        unique_together = [("company", "currency", "date")]
        ordering = ["-date"]

    def clean(self):
        if self.rate is not None and self.rate <= 0:
            raise ValidationError({"rate": "Rate must be positive."})

    @classmethod
    def rate_for(cls, company, currency_code, on_date) -> Decimal:
        if currency_code == company.base_currency_id:
            return Decimal("1")
        row = (cls.objects.filter(company=company, currency_id=currency_code, date__lte=on_date)
               .order_by("-date").first())
        if not row:
            raise ValidationError(f"No exchange rate for {currency_code} on or before {on_date}.")
        return row.rate


class CostCenter(CompanyScoped):
    """Analytic dimension: department / branch / project."""
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("company", "code")]
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"


class Sequence(models.Model):
    """Gapless document numbering. Always call `next_number` inside the transaction that
    creates the document, so a rollback also rolls back the counter."""
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    key = models.CharField(max_length=40)        # e.g. "journal:SAL", "doc:SO"
    year = models.PositiveIntegerField()
    next_value = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = [("company", "key", "year")]

    @classmethod
    def next_number(cls, company, key, year, prefix, padding=5) -> str:
        if not transaction.get_connection().in_atomic_block:
            raise RuntimeError("Sequence.next_number must run inside a transaction")
        seq, _ = cls.objects.get_or_create(company=company, key=key, year=year)
        seq = cls.objects.select_for_update().get(pk=seq.pk)
        value = seq.next_value
        seq.next_value = value + 1
        seq.save(update_fields=["next_value"])
        return f"{prefix}/{year}/{value:0{padding}d}"


class AuditLog(models.Model):
    """Append-only, hash-chained audit trail. A DB trigger blocks UPDATE and DELETE."""
    id = models.BigAutoField(primary_key=True)
    at = models.DateTimeField(default=timezone.now, db_index=True)
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                             related_name="+")
    actor_label = models.CharField(max_length=200, blank=True)  # username or api key name
    action = models.CharField(max_length=60, db_index=True)    # create / update / delete / post / login ...
    object_type = models.CharField(max_length=80, blank=True, db_index=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    object_repr = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    prev_hash = models.CharField(max_length=64, blank=True)
    hash = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["object_type", "object_id"])]

    def payload(self) -> str:
        return json.dumps({
            "at": self.at.isoformat(), "company": self.company_id, "user": self.user_id,
            "actor": self.actor_label, "action": self.action, "type": self.object_type,
            "oid": self.object_id, "changes": self.changes, "ip": self.ip,
        }, sort_keys=True, default=str)

    def compute_hash(self) -> str:
        return hashlib.sha256((self.prev_hash + self.payload()).encode()).hexdigest()

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Audit log entries are immutable.")
        with transaction.atomic():
            # Each company has its own independent hash chain (company_id=None actions --
            # system/superadmin events with no tenant -- share one chain keyed on 0). Serialize
            # writers *within that chain only*: a single global lock key here would serialize
            # every audited write across every tenant in the system, which becomes the write
            # throughput ceiling for the whole deployment as the number of companies/users
            # grows. Scoping the lock (and the "last hash" lookup below) to company_id lets
            # unrelated companies write their audit trails fully in parallel while still giving
            # each tenant a tamper-evident, fork-free chain.
            lock_key = self.company_id or 0
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", [727001, lock_key])
            last = (AuditLog.objects.filter(company_id=self.company_id)
                   .order_by("-id").values_list("hash", flat=True).first())
            self.prev_hash = last or ""
            self.hash = self.compute_hash()
            super().save(*args, **kwargs)

    @classmethod
    def verify_chain(cls, company_id=..., limit=None):
        """Returns (ok, first_broken_id). Chains are per-company (see save()); pass
        `company_id` to verify one tenant's chain (None = the system/non-tenant chain), or
        omit it to verify every chain in the table independently."""
        if company_id is ...:
            for cid in cls.objects.order_by().values_list("company_id", flat=True).distinct():
                ok, broken = cls.verify_chain(company_id=cid, limit=limit)
                if not ok:
                    return False, broken
            return True, None
        prev = ""
        qs = cls.objects.filter(company_id=company_id).order_by("id")
        if limit:
            qs = qs[:limit]
        for row in qs.iterator():
            if row.prev_hash != prev or row.compute_hash() != row.hash:
                return False, row.id
            prev = row.hash
        return True, None


def _attachment_path(instance, filename):
    import uuid
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"attachments/{instance.company_id}/{uuid.uuid4().hex}.{ext}"


class Attachment(models.Model):
    """Supporting documents (supplier invoice scans, contracts, customs declarations, bank advices).
    Files are only served through the authenticated API; attachments of posted documents cannot be deleted."""
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    object_type = models.CharField(max_length=60, db_index=True)
    object_id = models.BigIntegerField(db_index=True)
    file = models.FileField(upload_to=_attachment_path)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    description = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
