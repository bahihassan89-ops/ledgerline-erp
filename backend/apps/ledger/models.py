from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import MONEY, QTY, RATE, Company, CompanyScoped, CostCenter, Currency
from apps.core.signals import audited


class AccountClass(models.TextChoices):
    ASSET = "asset", "Asset"
    LIABILITY = "liability", "Liability"
    EQUITY = "equity", "Equity"
    INCOME = "income", "Revenue / Income"
    EXPENSE = "expense", "Expense"


DEBIT_NORMAL = {AccountClass.ASSET, AccountClass.EXPENSE}

# account_type -> class it must belong to
ACCOUNT_TYPES = {
    "group": None,  # non-postable parent; any class
    "cash": AccountClass.ASSET,
    "bank": AccountClass.ASSET,
    "receivable": AccountClass.ASSET,
    "current_asset": AccountClass.ASSET,
    "inventory": AccountClass.ASSET,
    "prepayment": AccountClass.ASSET,
    "tax_input": AccountClass.ASSET,
    "wht_receivable": AccountClass.ASSET,      # taxes withheld by customers (credit against income tax)
    "notes_receivable": AccountClass.ASSET,    # cheques received, not yet cleared (أوراق القبض)
    "wip": AccountClass.ASSET,                 # work in progress (إنتاج تحت التشغيل)
    "fixed_asset": AccountClass.ASSET,
    "accumulated_depreciation": AccountClass.ASSET,  # contra-asset (credit balance)
    "non_current_asset": AccountClass.ASSET,
    "payable": AccountClass.LIABILITY,
    "current_liability": AccountClass.LIABILITY,
    "tax_output": AccountClass.LIABILITY,
    "grni": AccountClass.LIABILITY,
    "wht_payable": AccountClass.LIABILITY,     # tax withheld from vendors, due to the Tax Authority
    "notes_payable": AccountClass.LIABILITY,   # cheques issued, not yet cleared (أوراق الدفع)
    "non_current_liability": AccountClass.LIABILITY,
    "equity": AccountClass.EQUITY,
    "retained_earnings": AccountClass.EQUITY,
    "revenue": AccountClass.INCOME,
    "other_income": AccountClass.INCOME,
    "cost_of_sales": AccountClass.EXPENSE,
    "expense": AccountClass.EXPENSE,
    "depreciation": AccountClass.EXPENSE,
    "other_expense": AccountClass.EXPENSE,
}
AccountType = models.TextChoices("AccountType", [(k, k.replace("_", " ").title()) for k in ACCOUNT_TYPES])
PL_CLASSES = {AccountClass.INCOME, AccountClass.EXPENSE}


@audited
class Account(CompanyScoped):
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=200)
    name_ar = models.CharField(max_length=200, blank=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    # Cash-flow statement classification; empty = derived from the account type.
    cash_flow_category = models.CharField(max_length=10, blank=True, choices=[
        ("operating", "Operating"), ("investing", "Investing"), ("financing", "Financing"), ("cash", "Cash"),
        ("none", "Excluded")])
    account_class = models.CharField(max_length=12, choices=AccountClass.choices)
    account_type = models.CharField(max_length=30, choices=AccountType.choices)
    level = models.PositiveSmallIntegerField(default=1, editable=False)
    # Reconcilable accounts track open items per partner (AR / AP).
    reconcilable = models.BooleanField(default=False)
    # Control accounts can only be posted by sub-ledgers (invoices, payments, stock), not manual entries.
    is_control = models.BooleanField(default=False)
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                 help_text="If set, lines on this account must be in this currency")
    requires_cost_center = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("company", "code")]
        ordering = ["code"]
        constraints = [models.CheckConstraint(condition=~Q(parent=models.F("id")), name="account_not_own_parent")]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def is_group(self):
        return self.account_type == "group"

    @property
    def debit_normal(self):
        return self.account_class in DEBIT_NORMAL

    def clean(self):
        errors = {}
        required_class = ACCOUNT_TYPES.get(self.account_type, "x")
        if required_class == "x":
            errors["account_type"] = "Unknown account type."
        elif required_class and required_class != self.account_class:
            errors["account_type"] = f"Type '{self.account_type}' belongs to class '{required_class}'."
        if self.parent_id:
            p = self.parent
            if p.company_id != self.company_id:
                errors["parent"] = "Parent belongs to another company."
            elif not p.is_group:
                errors["parent"] = "Parent must be a group (non-postable) account."
            elif p.account_class != self.account_class:
                errors["account_class"] = "Account class must match its parent's class."
            elif not self.code.startswith(p.code) or self.code == p.code:
                errors["code"] = f"Code must start with the parent code '{p.code}' and be longer."
            # cycle check
            node, seen = p, set()
            while node is not None:
                if node.pk == self.pk and self.pk:
                    errors["parent"] = "Circular hierarchy."
                    break
                if node.pk in seen:
                    break
                seen.add(node.pk)
                node = node.parent
        if self.pk:
            old = Account.objects.get(pk=self.pk)
            if JournalLine.objects.filter(account_id=self.pk).exists():
                if old.account_class != self.account_class:
                    errors["account_class"] = "Account has postings; its class cannot change."
                if self.is_group:
                    errors["account_type"] = "Account has postings and cannot become a group."
            if not self.is_group and self.children.exists():
                errors["account_type"] = "Account has child accounts; it must stay a group."
        if self.account_type in ("receivable", "payable"):
            self.reconcilable = True
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.level = (self.parent.level + 1) if self.parent_id else 1
        super().save(*args, **kwargs)

    def descendant_ids(self):
        ids, frontier = [self.pk], [self.pk]
        while frontier:
            frontier = list(Account.objects.filter(parent_id__in=frontier).values_list("id", flat=True))
            ids.extend(frontier)
        return ids


class JournalType(models.TextChoices):
    SALE = "sale", "Sales"
    PURCHASE = "purchase", "Purchases"
    BANK = "bank", "Bank"
    CASH = "cash", "Cash"
    INVENTORY = "inventory", "Inventory valuation"
    GENERAL = "general", "General / Miscellaneous"


@audited
class Journal(CompanyScoped):
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=12, choices=JournalType.choices)
    default_account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                        help_text="Bank/cash account for bank & cash journals")
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    # If False, users cannot create manual entries in this journal (sub-ledger journals).
    allow_manual = models.BooleanField(default=True)
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("company", "code")]
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class FiscalYear(CompanyScoped):
    name = models.CharField(max_length=40)
    start_date = models.DateField()
    end_date = models.DateField()
    closed = models.BooleanField(default=False)
    closing_entry = models.ForeignKey("JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+")

    class Meta:
        unique_together = [("company", "name")]
        ordering = ["-start_date"]
        constraints = [models.CheckConstraint(condition=Q(end_date__gt=models.F("start_date")), name="fy_dates")]

    def __str__(self):
        return self.name


class PeriodState(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed (privileged posting only)"
    LOCKED = "locked", "Locked (no posting)"


@audited
class FiscalPeriod(CompanyScoped):
    fiscal_year = models.ForeignKey(FiscalYear, on_delete=models.PROTECT, related_name="periods")
    number = models.PositiveSmallIntegerField()  # 1..12, 13 = adjustment period
    name = models.CharField(max_length=40)
    start_date = models.DateField()
    end_date = models.DateField()
    state = models.CharField(max_length=8, choices=PeriodState.choices, default=PeriodState.OPEN)

    class Meta:
        unique_together = [("fiscal_year", "number")]
        ordering = ["start_date"]

    def __str__(self):
        return self.name


@audited
class Tax(CompanyScoped):
    class Scope(models.TextChoices):
        SALE = "sale", "Sales"
        PURCHASE = "purchase", "Purchases"

    class Kind(models.TextChoices):
        VAT = "vat", "VAT (added to the price)"
        WITHHOLDING = "withholding", "Withholding tax (deducted from the amount due)"

    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    name_ar = models.CharField(max_length=120, blank=True)
    scope = models.CharField(max_length=10, choices=Scope.choices)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.VAT)
    rate = models.DecimalField(max_digits=7, decimal_places=4, help_text="Percent, e.g. 14 for 14%")
    # Egyptian Tax Authority e-invoice codes, e.g. T1/V009 (VAT general), T4/W004 (WHT services)
    eta_type = models.CharField(max_length=4, blank=True)
    eta_subtype = models.CharField(max_length=10, blank=True)
    # Withholding applies only when the document's untaxed amount (base currency) reaches this value.
    min_base = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    price_include = models.BooleanField(default=False)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("company", "code")]
        verbose_name_plural = "taxes"

    def __str__(self):
        return f"{self.code} ({self.rate}%)"


class AccountingSettings(models.Model):
    """Default accounts the sub-ledgers post to."""
    company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name="accounting")
    receivable_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    payable_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    grni_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    price_variance_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    inventory_adjustment_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    fx_gain_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    fx_loss_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    retained_earnings_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    opening_equity_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    cheques_receivable_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    cheques_payable_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    unrealized_fx_gain_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    unrealized_fx_loss_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    asset_disposal_gain_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    asset_disposal_loss_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    landed_cost_clearing_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    wip_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    labour_absorbed_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    overhead_absorbed_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    production_scrap_account = models.ForeignKey(Account, null=True, on_delete=models.PROTECT, related_name="+")
    depreciation_journal = models.ForeignKey(Journal, null=True, on_delete=models.PROTECT, related_name="+")
    opening_journal = models.ForeignKey(Journal, null=True, on_delete=models.PROTECT, related_name="+")
    sales_journal = models.ForeignKey(Journal, null=True, on_delete=models.PROTECT, related_name="+")
    purchase_journal = models.ForeignKey(Journal, null=True, on_delete=models.PROTECT, related_name="+")
    inventory_journal = models.ForeignKey(Journal, null=True, on_delete=models.PROTECT, related_name="+")
    general_journal = models.ForeignKey(Journal, null=True, on_delete=models.PROTECT, related_name="+")
    closing_journal = models.ForeignKey(Journal, null=True, on_delete=models.PROTECT, related_name="+")


class EntryState(models.TextChoices):
    DRAFT = "draft", "Draft"
    TO_APPROVE = "to_approve", "Waiting approval"
    APPROVED = "approved", "Approved"
    POSTED = "posted", "Posted"
    CANCELLED = "cancelled", "Cancelled"


# States in which an entry has no number and can still be edited / deleted.
UNPOSTED_STATES = ("draft", "to_approve", "approved")


class JournalEntry(CompanyScoped):
    journal = models.ForeignKey(Journal, on_delete=models.PROTECT, related_name="entries")
    number = models.CharField(max_length=40, blank=True, db_index=True)  # assigned at posting
    date = models.DateField(db_index=True)
    period = models.ForeignKey(FiscalPeriod, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reference = models.CharField(max_length=120, blank=True)
    narration = models.TextField(blank=True)
    state = models.CharField(max_length=10, choices=EntryState.choices, default=EntryState.DRAFT, db_index=True)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    rate = models.DecimalField(**RATE, default=Decimal("1"))
    # Origin document (invoice, payment, stock move ...). Manual entries have none.
    source_type = models.CharField(max_length=40, blank=True)
    source_id = models.BigIntegerField(null=True, blank=True)
    total_debit = models.DecimalField(**MONEY)
    total_credit = models.DecimalField(**MONEY)
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    posted_at = models.DateTimeField(null=True, blank=True)
    reversal_of = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="reversed_by")
    is_closing = models.BooleanField(default=False)
    # Accruals: when set, posting also posts the reversal on this date.
    auto_reverse_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name_plural = "journal entries"
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], condition=~Q(number=""), name="entry_number_unique"),
            models.CheckConstraint(condition=Q(state__in=UNPOSTED_STATES) | ~Q(number=""), name="posted_has_number"),
        ]
        indexes = [models.Index(fields=["source_type", "source_id"])]

    def __str__(self):
        return self.number or f"Draft #{self.pk}"

    @property
    def is_manual(self):
        return not self.source_type


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    sequence = models.PositiveIntegerField(default=0)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    partner = models.ForeignKey("partners.Partner", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="journal_lines")
    description = models.CharField(max_length=255, blank=True)
    debit = models.DecimalField(**MONEY)   # base currency
    credit = models.DecimalField(**MONEY)  # base currency
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    amount_currency = models.DecimalField(**MONEY)  # signed, transaction currency (+debit / -credit)
    cost_center = models.ForeignKey(CostCenter, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    tax = models.ForeignKey(Tax, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    tax_base = models.DecimalField(**MONEY)
    product = models.ForeignKey("inventory.Product", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+")
    quantity = models.DecimalField(**QTY)
    due_date = models.DateField(null=True, blank=True)
    # Denormalized for fast reporting; kept in sync by the posting service and checked by DB trigger.
    date = models.DateField(null=True, blank=True, db_index=True)
    posted = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["entry_id", "sequence", "id"]
        indexes = [models.Index(fields=["company", "account", "date"]),
                   models.Index(fields=["company", "partner", "account"])]
        constraints = [
            models.CheckConstraint(condition=Q(debit__gte=0) & Q(credit__gte=0), name="line_non_negative"),
            models.CheckConstraint(condition=~(Q(debit__gt=0) & Q(credit__gt=0)), name="line_one_side"),
        ]

    @property
    def balance(self):
        return self.debit - self.credit
