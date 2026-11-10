from django.db import models

from apps.core.models import MONEY, CompanyScoped
from apps.core.signals import audited


@audited
class Budget(CompanyScoped):
    name = models.CharField(max_length=120)
    fiscal_year = models.ForeignKey("ledger.FiscalYear", on_delete=models.PROTECT, related_name="budgets")
    state = models.CharField(max_length=10, default="draft", choices=[("draft", "Draft"), ("approved", "Approved")])
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [("company", "name")]
        ordering = ["-fiscal_year__start_date", "name"]

    def __str__(self):
        return self.name


class BudgetLine(models.Model):
    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+")
    cost_center = models.ForeignKey("core.CostCenter", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    period = models.ForeignKey("ledger.FiscalPeriod", on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(**MONEY)  # positive in the account's normal direction (income credit, expense debit)

    class Meta:
        unique_together = [("budget", "account", "cost_center", "period")]


@audited
class RecurringEntry(CompanyScoped):
    """Template for entries that repeat: rent, amortisation of prepayments, standing accruals."""
    name = models.CharField(max_length=120)
    journal = models.ForeignKey("ledger.Journal", on_delete=models.PROTECT, related_name="+")
    narration = models.CharField(max_length=255, blank=True)
    # [{"account": id, "debit": "100", "credit": "0", "description": "", "partner": id|null, "cost_center": id|null}]
    lines = models.JSONField(default=list)
    frequency = models.CharField(max_length=10, default="monthly", choices=[("monthly", "Monthly"),
                                                                           ("quarterly", "Quarterly"),
                                                                           ("yearly", "Yearly")])
    next_date = models.DateField()
    # The user's originally-intended day-of-month (e.g. "always run on the 31st"). `next_date` is a
    # mutable cursor that run_due() advances every period; once a short month forces it onto, say, the
    # 28th, the day alone can no longer tell us whether that's the real schedule or just a clamp from
    # a previous month. anchor_day preserves the real intent so later months can recover to it (see
    # services._add_months / run_due). Defaults to next_date's day; kept in sync with explicit edits to
    # next_date via the API (RecurringSerializer), but deliberately left untouched by run_due()'s own
    # internal advancement.
    anchor_day = models.PositiveSmallIntegerField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    auto_post = models.BooleanField(default=False, help_text="Post automatically (creator must hold posting rights)")
    auto_reverse_days = models.PositiveSmallIntegerField(null=True, blank=True,
                                                         help_text="Accrual: reverse N days after the entry date")
    active = models.BooleanField(default=True)
    last_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Safety net for instances created directly via the ORM (fixtures, tests, admin) that bypass
        # RecurringSerializer: give a freshly-created template a sane anchor instead of leaving it
        # None, which would otherwise crash the first run_due() advancement.
        if self.anchor_day is None and self.next_date:
            self.anchor_day = self.next_date.day
        super().save(*args, **kwargs)
