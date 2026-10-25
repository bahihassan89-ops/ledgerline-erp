"""Production: bills of materials, work centers and manufacturing orders (actual absorption costing)."""
from decimal import Decimal

from django.db import models
from django.db.models import Q

from apps.core.models import MONEY, QTY, CompanyScoped
from apps.core.signals import audited


@audited
class WorkCenter(CompanyScoped):
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    labour_rate = models.DecimalField(max_digits=20, decimal_places=4, default=Decimal("0"),
                                      help_text="Direct labour cost per hour (base currency)")
    overhead_rate = models.DecimalField(max_digits=20, decimal_places=4, default=Decimal("0"),
                                        help_text="Manufacturing overhead absorbed per hour")
    cost_center = models.ForeignKey("core.CostCenter", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("company", "code")]
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"


@audited
class Bom(CompanyScoped):
    """A BOM is versioned: editing components/operations of a version already used by an open
    manufacturing order is blocked (see BomSerializer.update) -- instead, `services.revise_bom()`
    creates a new version (same code, version+1) that starts as the active one, and deactivates the
    old version. Only one version per (company, code) may be active at a time."""
    code = models.CharField(max_length=40)
    version = models.PositiveIntegerField(default=1)
    revises = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="revisions",
                                help_text="The version this one supersedes")
    product = models.ForeignKey("inventory.Product", on_delete=models.PROTECT, related_name="boms")
    quantity = models.DecimalField(**QTY, help_text="Output quantity this recipe produces")
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [("company", "code", "version")]
        ordering = ["code", "-version"]
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="bom_qty_positive"),
                       models.UniqueConstraint(fields=["company", "code"], condition=Q(active=True),
                                               name="one_active_bom_version_per_code")]

    def __str__(self):
        return f"{self.code} v{self.version} — {self.product}"


class BomComponent(models.Model):
    bom = models.ForeignKey(Bom, on_delete=models.CASCADE, related_name="components")
    product = models.ForeignKey("inventory.Product", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(**QTY)
    scrap_pct = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"),
                                    help_text="Expected loss %, added to the planned quantity")

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="bom_component_qty_positive")]


class BomOperation(models.Model):
    bom = models.ForeignKey(Bom, on_delete=models.CASCADE, related_name="operations")
    sequence = models.PositiveIntegerField(default=10)
    name = models.CharField(max_length=120)
    work_center = models.ForeignKey(WorkCenter, on_delete=models.PROTECT, related_name="+")
    minutes = models.DecimalField(max_digits=12, decimal_places=2, help_text="Minutes for the BOM quantity")

    class Meta:
        ordering = ["sequence", "id"]


class MoState(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONFIRMED = "confirmed", "Confirmed"
    IN_PROGRESS = "in_progress", "In progress"
    DONE = "done", "Done"
    CANCELLED = "cancelled", "Cancelled"


class ManufacturingOrder(CompanyScoped):
    number = models.CharField(max_length=40, blank=True, db_index=True)
    product = models.ForeignKey("inventory.Product", on_delete=models.PROTECT, related_name="+")
    bom = models.ForeignKey(Bom, on_delete=models.PROTECT, related_name="orders")
    quantity = models.DecimalField(**QTY)
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")
    planned_date = models.DateField()
    state = models.CharField(max_length=12, choices=MoState.choices, default=MoState.DRAFT, db_index=True)
    produced_qty = models.DecimalField(**QTY)
    scrapped_qty = models.DecimalField(**QTY)
    # Actual cost build-up (base currency)
    material_cost = models.DecimalField(**MONEY)
    labour_cost = models.DecimalField(**MONEY)
    overhead_cost = models.DecimalField(**MONEY)
    output_value = models.DecimalField(**MONEY)
    scrap_value = models.DecimalField(**MONEY)
    date_done = models.DateField(null=True, blank=True)
    sales_order = models.ForeignKey("sales.SalesOrder", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-planned_date", "-id"]
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="mo_qty_positive")]

    def __str__(self):
        return self.number or f"MO draft #{self.pk}"

    @property
    def wip_balance(self):
        return self.material_cost + self.labour_cost + self.overhead_cost - self.output_value - self.scrap_value


class MoComponent(models.Model):
    mo = models.ForeignKey(ManufacturingOrder, on_delete=models.CASCADE, related_name="components")
    product = models.ForeignKey("inventory.Product", on_delete=models.PROTECT, related_name="+")
    planned_qty = models.DecimalField(**QTY)
    consumed_qty = models.DecimalField(**QTY)
    consumed_value = models.DecimalField(**MONEY)


class MoOperation(models.Model):
    mo = models.ForeignKey(ManufacturingOrder, on_delete=models.CASCADE, related_name="operations")
    sequence = models.PositiveIntegerField(default=10)
    name = models.CharField(max_length=120)
    work_center = models.ForeignKey(WorkCenter, on_delete=models.PROTECT, related_name="+")
    planned_minutes = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    actual_minutes = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    labour_cost = models.DecimalField(**MONEY)
    overhead_cost = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["sequence", "id"]
