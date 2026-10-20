from decimal import Decimal

from django.db import models
from django.db.models import Q

from apps.core.models import MONEY, QTY, CompanyScoped
from apps.core.signals import audited


class CostMethod(models.TextChoices):
    FIFO = "fifo", "FIFO (first in, first out)"
    AVERAGE = "average", "Weighted average (AVCO)"


@audited
class ProductCategory(CompanyScoped):
    name = models.CharField(max_length=120)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    cost_method = models.CharField(max_length=10, choices=CostMethod.choices, default=CostMethod.AVERAGE)
    inventory_account = models.ForeignKey("ledger.Account", null=True, blank=True, on_delete=models.PROTECT,
                                          related_name="+")
    cogs_account = models.ForeignKey("ledger.Account", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+")
    income_account = models.ForeignKey("ledger.Account", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="+")
    expense_account = models.ForeignKey("ledger.Account", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+", help_text="For non-stock purchases")

    class Meta:
        unique_together = [("company", "name")]
        verbose_name_plural = "product categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProductType(models.TextChoices):
    STOCK = "stock", "Stockable (tracked & valued)"
    CONSUMABLE = "consumable", "Consumable (expensed on purchase)"
    SERVICE = "service", "Service"


@audited
class Product(CompanyScoped):
    sku = models.CharField(max_length=60)
    name = models.CharField(max_length=200)
    name_ar = models.CharField(max_length=200, blank=True)
    # ETA item coding: GS1 barcode or EGS internal code registered on the ETA portal (e.g. EG-123456789-SKU1)
    eta_item_type = models.CharField(max_length=3, default="EGS", choices=[("EGS", "EGS"), ("GS1", "GS1")])
    eta_item_code = models.CharField(max_length=100, blank=True)
    eta_unit_type = models.CharField(max_length=10, default="EA", help_text="ETA unit code, e.g. EA, KGM, LTR, MTR")
    barcode = models.CharField(max_length=60, blank=True)
    type = models.CharField(max_length=12, choices=ProductType.choices, default=ProductType.STOCK)
    category = models.ForeignKey(ProductCategory, on_delete=models.PROTECT, related_name="products")
    uom = models.CharField(max_length=20, default="Unit")
    sale_price = models.DecimalField(**MONEY)
    purchase_price = models.DecimalField(**MONEY)
    sale_tax = models.ForeignKey("ledger.Tax", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    purchase_tax = models.ForeignKey("ledger.Tax", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    income_account = models.ForeignKey("ledger.Account", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="+")
    expense_account = models.ForeignKey("ledger.Account", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+")
    reorder_level = models.DecimalField(**QTY)
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("company", "sku")]
        ordering = ["sku"]

    def __str__(self):
        return f"[{self.sku}] {self.name}"

    @property
    def is_stock(self):
        return self.type == ProductType.STOCK

    # Account resolution: product override -> category -> error
    def account(self, kind):
        acc = getattr(self, f"{kind}_account", None) if kind in ("income", "expense") else None
        return acc or getattr(self.category, f"{kind}_account", None)


@audited
class Warehouse(CompanyScoped):
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=120)
    address = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("company", "code")]
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"


class StockQuant(models.Model):
    """Quantity on hand per product per warehouse."""
    company = models.ForeignKey("core.Company", on_delete=models.PROTECT, related_name="+")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="quants")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="quants")
    quantity = models.DecimalField(**QTY)

    class Meta:
        unique_together = [("product", "warehouse")]
        constraints = [models.CheckConstraint(condition=Q(quantity__gte=0), name="quant_non_negative")]


class ProductValuation(models.Model):
    """Company-level valuation of a product (all warehouses). Row is locked during moves."""
    company = models.ForeignKey("core.Company", on_delete=models.PROTECT, related_name="+")
    product = models.OneToOneField(Product, on_delete=models.PROTECT, related_name="valuation")
    quantity = models.DecimalField(**QTY)
    value = models.DecimalField(**MONEY)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gte=0), name="valuation_qty_non_negative")]

    @property
    def average_cost(self):
        return (self.value / self.quantity) if self.quantity else Decimal("0")


class MoveType(models.TextChoices):
    RECEIPT = "receipt", "Purchase receipt"
    DELIVERY = "delivery", "Sales delivery"
    ADJUST_IN = "adjust_in", "Adjustment (gain)"
    ADJUST_OUT = "adjust_out", "Adjustment (loss)"
    CUSTOMER_RETURN = "customer_return", "Customer return"
    VENDOR_RETURN = "vendor_return", "Return to vendor"
    OPENING = "opening", "Opening stock"
    TRANSFER_IN = "transfer_in", "Transfer in"
    TRANSFER_OUT = "transfer_out", "Transfer out"
    PRODUCTION_CONSUME = "production_consume", "Consumed in production"
    PRODUCTION_OUTPUT = "production_output", "Produced (finished goods)"


INBOUND = {MoveType.RECEIPT, MoveType.ADJUST_IN, MoveType.CUSTOMER_RETURN, MoveType.OPENING, MoveType.TRANSFER_IN,
           MoveType.PRODUCTION_OUTPUT}


class StockMove(CompanyScoped):
    number = models.CharField(max_length=40, blank=True)
    date = models.DateField()
    move_type = models.CharField(max_length=20, choices=MoveType.choices)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="moves")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="moves")
    quantity = models.DecimalField(**QTY)  # always positive; direction from move_type
    unit_cost = models.DecimalField(max_digits=20, decimal_places=6, default=Decimal("0"))  # base currency
    value = models.DecimalField(**MONEY)  # base currency, always positive
    partner = models.ForeignKey("partners.Partner", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reference = models.CharField(max_length=120, blank=True)
    purchase_line = models.ForeignKey("purchasing.PurchaseOrderLine", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="moves")
    sales_line = models.ForeignKey("sales.SalesOrderLine", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="moves")
    manufacturing_order = models.ForeignKey("manufacturing.ManufacturingOrder", null=True, blank=True,
                                            on_delete=models.PROTECT, related_name="moves")
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="move_qty_positive")]

    @property
    def inbound(self):
        return self.move_type in INBOUND

    def __str__(self):
        return self.number or f"Move #{self.pk}"


class CostLayer(models.Model):
    """One layer per inbound move. FIFO consumes `remaining_qty` oldest first."""
    company = models.ForeignKey("core.Company", on_delete=models.PROTECT, related_name="+")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="layers")
    move = models.OneToOneField(StockMove, on_delete=models.PROTECT, related_name="layer")
    date = models.DateField()
    quantity = models.DecimalField(**QTY)
    unit_cost = models.DecimalField(max_digits=20, decimal_places=6)
    value = models.DecimalField(**MONEY)
    remaining_qty = models.DecimalField(**QTY)
    remaining_value = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["date", "id"]
        constraints = [models.CheckConstraint(condition=Q(remaining_qty__gte=0), name="layer_remaining_non_negative")]


class LandedCost(CompanyScoped):
    """Customs duties, freight, clearance and insurance capitalised into the cost of received goods
    (EAS 2 / IAS 2). The service bills are posted to the Landed Costs Clearing account; this document
    moves that amount into inventory (and COGS for any part already sold)."""
    number = models.CharField(max_length=40, blank=True)
    date = models.DateField()
    description = models.CharField(max_length=255)
    amount = models.DecimalField(**MONEY)  # base currency
    allocation = models.CharField(max_length=10, default="value", choices=[("value", "By value"),
                                                                          ("quantity", "By quantity")])
    counter_account = models.ForeignKey("ledger.Account", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+", help_text="Default: Landed Costs Clearing")
    moves = models.ManyToManyField(StockMove, related_name="landed_costs")
    state = models.CharField(max_length=10, default="draft", choices=[("draft", "Draft"), ("posted", "Posted")])
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="landed_cost_positive")]

    def __str__(self):
        return self.number or f"Landed cost #{self.pk}"
