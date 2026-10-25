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
    # Capacity planning / finite scheduling (see manufacturing.services.schedule_operations): the work
    # center can do `capacity_minutes_per_day` of work on each of `working_weekdays` (Python weekday
    # numbers, Monday=0 .. Sunday=6); specific dates can override that via WorkCenterCalendarException
    # (holidays, extra shifts, maintenance days).
    capacity_minutes_per_day = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("480"),
                                                    help_text="Minutes of work this center can absorb per working "
                                                              "day (default 8-hour shift)")
    working_weekdays = models.CharField(max_length=20, default="0,1,2,3,4",
                                        help_text="Comma-separated weekday numbers the center normally runs "
                                                  "(Monday=0 .. Sunday=6); default Mon-Fri")
    # Subcontracting: an "external" work center operated by a vendor instead of in-house labour/
    # overhead -- see manufacturing.services.record_subcontract_cost.
    is_subcontracted = models.BooleanField(default=False,
                                           help_text="Operations here are performed by an outside vendor; "
                                                     "cost is the vendor's invoiced charge, not labour/overhead "
                                                     "rates.")
    default_vendor = models.ForeignKey("partners.Partner", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="+")
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("company", "code")]
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def working_weekday_set(self):
        return {int(x) for x in self.working_weekdays.split(",") if x.strip() != ""}


@audited
class WorkCenterCalendarException(CompanyScoped):
    """Overrides a work center's normal daily capacity for one specific date: 0 minutes for a
    holiday/shutdown, or a different figure for an extra shift or a half day."""
    work_center = models.ForeignKey(WorkCenter, on_delete=models.CASCADE, related_name="calendar_exceptions")
    date = models.DateField()
    available_minutes = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = [("work_center", "date")]
        ordering = ["date"]

    def __str__(self):
        return f"{self.work_center.code} {self.date}: {self.available_minutes}min"


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
    backflush = models.BooleanField(default=False,
                                    help_text="Auto-consume component quantities (at BOM ratios) when output is "
                                              "recorded, instead of requiring an explicit consume() first. "
                                              "Components that are themselves lot-tracked still need to be "
                                              "consumed manually first, for traceability.")
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
    # Alternate routing: if the primary work center has no free capacity on the day the scheduler
    # reaches this operation, it tries these (in order) instead -- see services.schedule_operations.
    alternate_work_centers = models.ManyToManyField(WorkCenter, blank=True, related_name="+")

    class Meta:
        ordering = ["sequence", "id"]


class BomByproduct(models.Model):
    """A secondary output recovered from the same production run as the BOM's main product (e.g.
    offcuts, by-product credited back at a standard value). `quantity` is per `bom.quantity` of main
    output, mirroring BomComponent. `unit_value` is the standard recovery value per unit, since a
    by-product has no BOM/average cost of its own -- it is valued the same way scrap would be."""
    bom = models.ForeignKey(Bom, on_delete=models.CASCADE, related_name="byproducts")
    product = models.ForeignKey("inventory.Product", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(**QTY)
    unit_value = models.DecimalField(max_digits=20, decimal_places=6, default=Decimal("0"))

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="bom_byproduct_qty_positive")]


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
    byproduct_value = models.DecimalField(**MONEY, help_text="Recovery value of by-products produced, credited "
                                                              "against WIP (reduces the main product's cost)")
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
        return (self.material_cost + self.labour_cost + self.overhead_cost - self.output_value - self.scrap_value
                - self.byproduct_value)


class MoComponent(models.Model):
    mo = models.ForeignKey(ManufacturingOrder, on_delete=models.CASCADE, related_name="components")
    product = models.ForeignKey("inventory.Product", on_delete=models.PROTECT, related_name="+")
    planned_qty = models.DecimalField(**QTY)
    consumed_qty = models.DecimalField(**QTY)
    consumed_value = models.DecimalField(**MONEY)


class MoByproduct(models.Model):
    mo = models.ForeignKey(ManufacturingOrder, on_delete=models.CASCADE, related_name="byproducts")
    product = models.ForeignKey("inventory.Product", on_delete=models.PROTECT, related_name="+")
    planned_qty = models.DecimalField(**QTY)
    produced_qty = models.DecimalField(**QTY)
    # Copied from BomByproduct.unit_value at confirm() time, so services._produce_byproducts can value
    # the receipt even after the BOM is later revised/changed.
    unit_value = models.DecimalField(max_digits=20, decimal_places=6, default=Decimal("0"))
    value = models.DecimalField(**MONEY)


class MoOperation(models.Model):
    mo = models.ForeignKey(ManufacturingOrder, on_delete=models.CASCADE, related_name="operations")
    sequence = models.PositiveIntegerField(default=10)
    name = models.CharField(max_length=120)
    work_center = models.ForeignKey(WorkCenter, on_delete=models.PROTECT, related_name="+")
    # Copied from BomOperation.alternate_work_centers at confirm() time, so the schedule can swap to
    # one of these if the primary work center has no free capacity -- see services.schedule_operations.
    alternate_work_centers = models.ManyToManyField(WorkCenter, blank=True, related_name="+")
    planned_minutes = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    actual_minutes = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    labour_cost = models.DecimalField(**MONEY)
    overhead_cost = models.DecimalField(**MONEY)
    subcontract_cost = models.DecimalField(**MONEY, help_text="Vendor charge booked via "
                                                               "services.record_subcontract_cost, for operations "
                                                               "at a subcontracted work center")
    scheduled_start = models.DateField(null=True, blank=True)
    scheduled_end = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["sequence", "id"]


class QualityInspection(CompanyScoped):
    """A quality-control inspection event for a manufacturing order: the quality gate for products
    with `Product.requires_qc` -- services.produce() will not release more finished-good units than
    have been inspected and passed here (see services._consume_quality_pass)."""
    mo = models.ForeignKey(ManufacturingOrder, on_delete=models.CASCADE, related_name="quality_inspections")
    qty_passed = models.DecimalField(**QTY)
    qty_failed = models.DecimalField(**QTY)
    qty_used = models.DecimalField(**QTY, help_text="How much of qty_passed has already been turned into "
                                                     "produced output")
    notes = models.TextField(blank=True)
    inspected_at = models.DateField()

    class Meta:
        ordering = ["inspected_at", "id"]
        constraints = [models.CheckConstraint(condition=Q(qty_used__lte=models.F("qty_passed")),
                                              name="qc_used_not_over_passed")]
