from django.db import models

from apps.core.models import MONEY, CompanyScoped


class BankStatement(CompanyScoped):
    journal = models.ForeignKey("ledger.Journal", on_delete=models.PROTECT, related_name="statements")
    reference = models.CharField(max_length=120, blank=True)
    date_from = models.DateField()
    date_to = models.DateField()
    opening_balance = models.DecimalField(**MONEY)
    closing_balance = models.DecimalField(**MONEY)
    state = models.CharField(max_length=12, default="open", choices=[("open", "Open"), ("reconciled", "Reconciled")])

    class Meta:
        ordering = ["-date_to", "-id"]

    def __str__(self):
        return f"{self.journal.code} {self.date_from}–{self.date_to}"


class BankStatementLine(models.Model):
    statement = models.ForeignKey(BankStatement, on_delete=models.CASCADE, related_name="lines")
    date = models.DateField()
    description = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=120, blank=True)
    amount = models.DecimalField(**MONEY)  # + money in, - money out (statement currency = journal currency)
    partner = models.ForeignKey("partners.Partner", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # One statement line ↔ one posted GL line on the bank account (posted lines stay immutable).
    matched_line = models.OneToOneField("ledger.JournalLine", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="bank_match")
    matched_by = models.CharField(max_length=10, blank=True)  # auto / manual / created

    class Meta:
        ordering = ["date", "id"]
