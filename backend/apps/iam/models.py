from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.crypto import EncryptedTextField
from apps.core.models import Company
from apps.core.signals import audited

from .permissions_catalog import CATALOG, GLOBAL_CATALOG


@audited
class GlobalRole(models.Model):
    """A company-independent role (CEO, central Controller, central Accounting Manager).
    Assigned directly to a User via M2M -- it expresses authority that spans every company
    rather than one company's Role/Membership, e.g. giving final approval to activate a new
    company or viewing the team-activity portal across all companies."""
    name = models.CharField(max_length=80, unique=True)
    description = models.CharField(max_length=255, blank=True)
    level = models.PositiveSmallIntegerField(default=1, help_text="Managerial level 0-5")
    permissions = models.JSONField(default=list)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-level", "name"]

    def __str__(self):
        return f"{self.name} (L{self.level})"

    def clean(self):
        unknown = set(self.permissions) - set(GLOBAL_CATALOG)
        if unknown:
            raise ValidationError({"permissions": f"Unknown global permissions: {', '.join(sorted(unknown))}"})
        if self.level > 5:
            raise ValidationError({"level": "Level must be 0-5."})


class User(AbstractUser):
    email = models.EmailField(unique=True)
    mfa_enabled = models.BooleanField(default=False)
    mfa_secret = EncryptedTextField(blank=True, default="")
    mfa_last_counter = models.BigIntegerField(default=0)  # replay protection for TOTP
    failed_login_count = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    must_change_password = models.BooleanField(default=False)
    global_roles = models.ManyToManyField(GlobalRole, blank=True, related_name="users")

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

    # --- authorization helpers (cross-company / central) ---
    def global_permissions(self) -> set:
        if self.is_superuser:
            return set(GLOBAL_CATALOG)
        perms = set()
        for r in self.global_roles.filter(active=True):
            perms.update(r.permissions)
        return perms

    def has_global_perm(self, code) -> bool:
        return code in self.global_permissions()


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


ONLINE_WINDOW = timezone.timedelta(minutes=2)
AWAY_WINDOW = timezone.timedelta(minutes=30)


class UserPresence(models.Model):
    """Live 'where is everyone right now' cache -- one row per user, overwritten on every
    heartbeat. The permanent record is PresenceEvent below; this table only answers 'now'."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="presence")
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    module_code = models.CharField(max_length=40, blank=True)
    section = models.CharField(max_length=120, blank=True, help_text="Human label, e.g. 'Billing → Vendor bills'")
    last_heartbeat = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True, help_text="Set on explicit logout/tab-close signal.")

    def status(self) -> str:
        if self.ended_at and self.ended_at >= self.last_heartbeat:
            return "offline"
        idle = timezone.now() - self.last_heartbeat
        if idle <= ONLINE_WINDOW:
            return "online"
        if idle <= AWAY_WINDOW:
            return "away"
        return "offline"

    def __str__(self):
        return f"{self.user} @ {self.company or '—'}"


class PresenceEvent(models.Model):
    """Permanent, append-only historical log of presence transitions per user -- protected by
    the same erp_append_only() Postgres trigger already used for core_auditlog (see
    0003_presence_log_and_global_roles migration): this table is never pruned and never editable,
    even by direct SQL, satisfying the 'historical log saved per user at all times' requirement."""
    id = models.BigAutoField(primary_key=True)
    at = models.DateTimeField(default=timezone.now, db_index=True)
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="presence_events")
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    module_code = models.CharField(max_length=40, blank=True)
    section = models.CharField(max_length=120, blank=True)
    event = models.CharField(max_length=20, choices=[("login", "Login"), ("switch", "Switched context"),
                                                      ("heartbeat", "Heartbeat checkpoint"), ("logout", "Logout")])

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["user", "at"])]

    def __str__(self):
        return f"{self.user} {self.event} @ {self.at}"


class DailyActivitySummary(models.Model):
    """Fast aggregate ('active today') derived from PresenceEvent/heartbeats, so the team-activity
    portal doesn't have to sum the permanent log on every page load."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="daily_activity")
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    date = models.DateField()
    active_minutes = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    class Meta:
        unique_together = [("user", "company", "date")]
        ordering = ["-date"]

    def __str__(self):
        return f"{self.user} {self.date}: {self.active_minutes}m"
