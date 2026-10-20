from decimal import Decimal

from django.db import models
from django.db.models import Q, Sum

from apps.core.models import MONEY, CompanyScoped, CostCenter
from apps.core.signals import audited


class Method(models.TextChoices):
    STRAIGHT_LINE = "straight_line", "Straight line (القسط الثابت)"
    DECLINING = "declining_balance", "Declining balance (القسط المتناقص)"


class TaxMethod(models.TextChoices):
    STRAIGHT_ON_COST = "straight_on_cost", "Fixed % of cost each year (buildings 5%, intangibles 10%)"
    POOL_DECLINING = "pool_declining", "% of the depreciation basis each year (computers 50%, other assets 25%)"
    NONE = "none", "Not depreciable for tax (land)"


@audited
class AssetCategory(CompanyScoped):
    name = models.CharField(max_length=120)
    name_ar = models.CharField(max_length=120, blank=True)
    asset_account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+")
    accumulated_account = models.ForeignKey("ledger.Account", null=True, blank=True, on_delete=models.PROTECT,
                                            related_name="+")
    expense_account = models.ForeignKey("ledger.Account", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+")
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.STRAIGHT_LINE)
    useful_life_months = models.PositiveIntegerField(default=60)
    declining_rate = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"),
                                         help_text="Annual % for declining balance")
    # Egyptian Income Tax Law 91/2005, article 25
    tax_method = models.CharField(max_length=20, choices=TaxMethod.choices, default=TaxMethod.POOL_DECLINING)
    tax_rate_pct = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("25"))

    class Meta:
        unique_together = [("company", "name")]
        verbose_name_plural = "asset categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class AssetState(models.TextChoices):
    DRAFT = "draft", "Draft"
    RUNNING = "running", "In service"
    FULLY_DEPRECIATED = "fully_depreciated", "Fully depreciated"
    DISPOSED = "disposed", "Disposed"


@audited
class Asset(CompanyScoped):
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    name_ar = models.CharField(max_length=200, blank=True)
    category = models.ForeignKey(AssetCategory, on_delete=models.PROTECT, related_name="assets")
    serial_number = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=120, blank=True)
    custodian = models.CharField(max_length=120, blank=True, help_text="Employee responsible (العهدة)")
    cost_center = models.ForeignKey(CostCenter, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    acquisition_date = models.DateField()
    in_service_date = models.DateField()
    # First month this system depreciates (go-live assets: the go-live month)
    depreciation_start = models.DateField(null=True, blank=True)
    cost = models.DecimalField(**MONEY)
    salvage_value = models.DecimalField(**MONEY)
    opening_accumulated = models.DecimalField(**MONEY, help_text="Depreciation booked before go-live")
    useful_life_months = models.PositiveIntegerField()
    method = models.CharField(max_length=20, choices=Method.choices)
    declining_rate = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))
    state = models.CharField(max_length=20, choices=AssetState.choices, default=AssetState.DRAFT, db_index=True)
    bill_line = models.OneToOneField("billing.InvoiceLine", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="asset")
    disposal_date = models.DateField(null=True, blank=True)
    disposal_proceeds = models.DecimalField(**MONEY)
    disposal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="+")
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [("company", "code")]
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(condition=Q(cost__gt=0), name="asset_cost_positive"),
            models.CheckConstraint(condition=Q(salvage_value__gte=0) & Q(salvage_value__lt=models.F("cost")),
                                   name="asset_salvage_range"),
        ]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def posted_depreciation(self):
        return self.lines.filter(posted=True).aggregate(s=Sum("amount"))["s"] or Decimal("0")

    @property
    def accumulated(self):
        return self.opening_accumulated + self.posted_depreciation

    @property
    def net_book_value(self):
        return self.cost - self.accumulated


class DepreciationLine(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="lines")
    date = models.DateField()  # month end
    amount = models.DecimalField(**MONEY)
    posted = models.BooleanField(default=False)
    entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")

    class Meta:
        ordering = ["date"]
        unique_together = [("asset", "date")]
