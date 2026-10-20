from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.crypto import EncryptedTextField
from apps.core.models import Company
from apps.core.signals import audited

from .permissions_catalog import CATALOG


class User(AbstractUser):
    email = models.EmailField(unique=True)
    mfa_enabled = models.BooleanField(default=False)
    mfa_secret = EncryptedTextField(blank=True, default="")
    mfa_last_counter = models.BigIntegerField(default=0)  # replay protection for TOTP
    failed_login_count = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    must_change_password = models.BooleanField(default=False)

    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())

    def set_password(self, raw_password):
        super().set_password(raw_password)
        self.password_changed_at = timezone.now()

    # --- authorization helpers (per company) ---
    def membership(self, company_id):
        return (self.memberships.filter(company_id=company_id, active=True, company__active=True)
                .prefetch_related("roles").first())

    def permissions_for(self, company_id) -> set:
        if self.is_superuser:
            return set(CATALOG)
        m = self.membership(company_id)
        if not m:
            return set()
        perms = set()
        for r in m.roles.all():
            if r.active:
                perms.update(r.permissions)
        return perms

    def level_for(self, company_id) -> int:
        m = self.membership(company_id)
        return max((r.level for r in m.roles.all() if r.active), default=0) if m else 0

    def approval_limit_for(self, company_id):
        """Highest approval limit across active roles. None = unlimited."""
        if self.is_superuser:
            return None
        m = self.membership(company_id)
        if not m:
            return Decimal("0")
        limits = [r.approval_limit for r in m.roles.all() if r.active]
        if not limits:
            return Decimal("0")
        if any(lim is None for lim in limits):
            return None
        return max(limits)


@audited
class Role(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="roles")
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=255, blank=True)
    level = models.PositiveSmallIntegerField(default=1, help_text="Managerial level 0-5")
    permissions = models.JSONField(default=list)
    approval_limit = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True,
                                         help_text="Max document amount this role may approve. Empty = unlimited.")
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("company", "name")]
        ordering = ["-level", "name"]

    def __str__(self):
        return f"{self.name} (L{self.level})"

    def clean(self):
        unknown = set(self.permissions) - set(CATALOG)
        if unknown:
            raise ValidationError({"permissions": f"Unknown permissions: {', '.join(sorted(unknown))}"})
        if self.level > 5:
            raise ValidationError({"level": "Level must be 0-5."})


@audited
class Membership(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="memberships")
    roles = models.ManyToManyField(Role, blank=True)
    active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        unique_together = [("user", "company")]

    def __str__(self):
        return f"{self.user} @ {self.company}"
