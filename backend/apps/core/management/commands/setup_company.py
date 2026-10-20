"""Create a fully configured company.

    python manage.py setup_company --name "Acme Trading SAE" --code acme --country EG
    python manage.py setup_company --name "Acme LLC" --code acme-sa --currency SAR --vat 15
"""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.approvals.models import ApprovalRule
from apps.core.models import Company, Currency
from apps.iam.models import Role
from apps.iam.permissions_catalog import DEFAULT_ROLES
from apps.inventory.models import ProductCategory, Warehouse
from apps.ledger.models import Account, AccountingSettings, Journal, Tax
from apps.ledger.services import create_fiscal_year
from apps.partners.models import PaymentTerm

from ..coa import CHART, DEFAULT_ACCOUNTS

CURRENCIES = [("EGP", "Egyptian Pound", "ج.م", 2), ("USD", "US Dollar", "$", 2), ("EUR", "Euro", "€", 2),
              ("GBP", "Pound Sterling", "£", 2), ("SAR", "Saudi Riyal", "SR", 2), ("AED", "UAE Dirham", "AED", 2),
              ("QAR", "Qatari Riyal", "QR", 2), ("KWD", "Kuwaiti Dinar", "KD", 3), ("BHD", "Bahraini Dinar", "BD", 3),
              ("OMR", "Omani Rial", "OMR", 3), ("JOD", "Jordanian Dinar", "JD", 3), ("CNY", "Chinese Yuan", "¥", 2)]

JOURNALS = [
    ("SAL", "Customer Invoices", "sale", None, False),
    ("PUR", "Vendor Bills", "purchase", None, False),
    ("BNK", "Main Bank", "bank", "110110", True),
    ("BNK2", "Foreign Currency Bank", "bank", "110111", True),
    ("CSH", "Cash Box", "cash", "110101", True),
    ("STK", "Inventory Valuation", "inventory", None, False),
    ("GEN", "General Journal", "general", None, True),
    ("DEP", "Depreciation", "general", None, False),
    ("OPN", "Opening Balances", "general", None, True),
    ("CLS", "Year-End Closing", "general", None, False),
]

# Egypt (Law 67/2016 VAT; Income Tax Law 91/2005 art. 59 & Ministerial Decree 405/2018 withholding).
# (code, name, name_ar, scope, kind, rate, eta_type, eta_subtype, account, min_base)
EGYPT_TAXES = [
    ("VAT14-S", "VAT 14% (sales)", "ضريبة القيمة المضافة 14% مبيعات", "sale", "vat", 14, "T1", "V009", "210301", 0),
    ("VAT5-S", "VAT 5% machinery & equipment (sales)", "ضريبة قيمة مضافة 5% آلات ومعدات", "sale", "vat", 5, "T1",
     "V010", "210301", 0),
    ("VAT0-EXP", "Zero-rated export", "تصدير — سعر صفر", "sale", "vat", 0, "T1", "V001", "210301", 0),
    ("VAT-EX-S", "Exempt supply (sales)", "سلعة / خدمة معفاة", "sale", "vat", 0, "T1", "V003", "210301", 0),
    ("VAT14-P", "VAT 14% (purchases)", "ضريبة القيمة المضافة 14% مشتريات", "purchase", "vat", 14, "T1", "V009",
     "110403", 0),
    ("VAT5-P", "VAT 5% machinery & equipment (purchases)", "ضريبة قيمة مضافة 5% مشتريات آلات", "purchase", "vat",
     5, "T1", "V010", "110403", 0),
    ("VAT-EX-P", "Exempt / non-taxable purchase", "مشتريات معفاة", "purchase", "vat", 0, "T1", "V003", "110403", 0),
    # Withholding we deduct from vendors (we pay it to the Tax Authority with Form 41)
    ("WHT1-CON-P", "WHT 1% contracting", "خصم وإضافة 1% مقاولات", "purchase", "withholding", 1, "T4", "W001",
     "210304", 300),
    ("WHT1-SUP-P", "WHT 1% supplies & purchases", "خصم وإضافة 1% توريدات ومشتريات", "purchase", "withholding", 1,
     "T4", "W002", "210304", 300),
    ("WHT3-SRV-P", "WHT 3% services", "خصم وإضافة 3% خدمات", "purchase", "withholding", 3, "T4", "W004",
     "210304", 300),
    ("WHT5-COM-P", "WHT 5% commissions & brokerage", "خصم وإضافة 5% عمولات وسمسرة", "purchase", "withholding", 5,
     "T4", "W011", "210304", 300),
    # Withholding our customers deduct from our invoices (credit against our income tax)
    ("WHT1-SUP-S", "WHT 1% supplies (deducted by customer)", "خصم وإضافة 1% توريدات (لدى العميل)", "sale",
     "withholding", 1, "T4", "W002", "110206", 300),
    ("WHT3-SRV-S", "WHT 3% services (deducted by customer)", "خصم وإضافة 3% خدمات (لدى العميل)", "sale",
     "withholding", 3, "T4", "W004", "110206", 300),
    ("WHT5-COM-S", "WHT 5% commissions (deducted by customer)", "خصم وإضافة 5% عمولات (لدى العميل)", "sale",
     "withholding", 5, "T4", "W011", "110206", 300),
]


def ensure_currencies():
    for code, name, sym, dec in CURRENCIES:
        Currency.objects.get_or_create(code=code, defaults={"name": name, "symbol": sym, "decimals": dec})


def sync_chart(company):
    """Create any standard account that is missing (by code). Never changes existing accounts."""
    by_code = {a.code: a for a in Account.objects.filter(company=company)}

    def make(node, parent, cls):
        code, name, name_ar, typ = node[:4]
        rest = node[4:]
        if parent is None:
            cls, rest = rest[0], rest[1:]
        children = rest[-1] if rest and isinstance(rest[-1], list) else []
        flags = [f for f in rest if isinstance(f, str)]
        a = by_code.get(code)
        if a is None:
            a = Account(company=company, code=code, name=name, name_ar=name_ar, parent=parent, account_class=cls,
                        account_type=typ, is_control="control" in flags)
            a.clean()
            a.save()
            by_code[code] = a
        elif not a.name_ar:
            Account.objects.filter(pk=a.pk).update(name_ar=name_ar)
        for ch in children:
            make(ch, a, cls)

    for root in CHART:
        make(root, None, None)
    return by_code


def sync_settings(company, acc):
    st, _ = AccountingSettings.objects.get_or_create(company=company)
    for field, code in DEFAULT_ACCOUNTS.items():
        if getattr(st, f"{field}_id") is None and code in acc:
            setattr(st, field, acc[code])
    J = {j.code: j for j in Journal.objects.filter(company=company)}
    for code, name, typ, default, manual in JOURNALS:
        if code not in J:
            J[code] = Journal.objects.create(company=company, code=code, name=name, type=typ, allow_manual=manual,
                                             default_account=acc.get(default) if default else None)
    for field, code in [("sales_journal", "SAL"), ("purchase_journal", "PUR"), ("inventory_journal", "STK"),
                        ("general_journal", "GEN"), ("closing_journal", "CLS"), ("depreciation_journal", "DEP"),
                        ("opening_journal", "OPN")]:
        if getattr(st, f"{field}_id") is None:
            setattr(st, field, J[code])
    st.save()
    return st, J


class Command(BaseCommand):
    help = "Create a company with chart of accounts, journals, taxes, roles and the current fiscal year"

    def add_arguments(self, p):
        p.add_argument("--name", required=True)
        p.add_argument("--name-ar", default="")
        p.add_argument("--code", required=True)
        p.add_argument("--country", default="", help="EG = Egyptian profile (EGP, VAT 14%%, withholding, ETA)")
        p.add_argument("--currency", default=None)
        p.add_argument("--vat", default=None, help="Standard VAT rate in percent (non-Egypt profile)")
        p.add_argument("--fy-start-month", type=int, default=1)
        p.add_argument("--year", type=int, default=None)

    @transaction.atomic
    def handle(self, *a, **o):
        ensure_currencies()
        egypt = o["country"].upper() == "EG"
        currency = o["currency"] or ("EGP" if egypt else "USD")
        if Company.objects.filter(code=o["code"]).exists():
            raise CommandError("A company with this code already exists.")
        if not Currency.objects.filter(code=currency).exists():
            raise CommandError(f"Unknown currency {currency}")
        company = Company.objects.create(name=o["name"], name_ar=o["name_ar"], legal_name=o["name"], code=o["code"],
                                         base_currency_id=currency, country=o["country"].upper(),
                                         fiscal_year_start_month=o["fy_start_month"])
        vat = Decimal(o["vat"]) if o["vat"] is not None else Decimal("14" if egypt else "15")
        setup(company, vat, o["year"] or timezone.localdate().year, egypt=egypt)
        self.stdout.write(self.style.SUCCESS(f"Company '{company.name}' created (id={company.pk})."))


def setup(company, vat, year, egypt=None):
    egypt = company.country == "EG" if egypt is None else egypt
    acc = sync_chart(company)
    st, J = sync_settings(company, acc)
    taxes = {}
    if egypt:
        for code, name, name_ar, scope, kind, rate, et, es, account, min_base in EGYPT_TAXES:
            taxes[code] = Tax.objects.create(company=company, code=code, name=name, name_ar=name_ar, scope=scope,
                                             kind=kind, rate=rate, eta_type=et, eta_subtype=es,
                                             account=acc[account], min_base=min_base)
        taxes["S"], taxes["P"] = taxes["VAT14-S"], taxes["VAT14-P"]
    elif vat:
        taxes["S"] = Tax.objects.create(company=company, code=f"VAT{vat:g}-S", name=f"VAT {vat:g}% (sales)",
                                        scope="sale", rate=vat, account=acc["210301"], eta_type="T1",
                                        eta_subtype="V009")
        taxes["P"] = Tax.objects.create(company=company, code=f"VAT{vat:g}-P", name=f"VAT {vat:g}% (purchases)",
                                        scope="purchase", rate=vat, account=acc["110403"])
        Tax.objects.create(company=company, code="VAT0-S", name="Zero-rated (sales)", scope="sale", rate=0,
                           account=acc["210301"])
        Tax.objects.create(company=company, code="EXEMPT-P", name="Exempt (purchases)", scope="purchase", rate=0,
                           account=acc["110403"])
    for name, level, perms, limit in DEFAULT_ROLES:
        Role.objects.get_or_create(company=company, name=name, defaults={
            "level": level, "permissions": sorted(set(perms)),
            "approval_limit": Decimal(limit) if limit is not None else None})
    k = Decimal(10) if company.base_currency_id == "EGP" else Decimal(1)  # thresholds scaled for EGP
    for name, dtype, amount, levels in [
        ("PO above threshold → Controller", "purchase_order", 50000, [4]),
        ("Large PO → Controller + CFO", "purchase_order", 250000, [4, 5]),
        ("Large vendor bill → Controller", "vendor_bill", 250000, [4]),
        ("Payment above threshold → Controller", "payment", 100000, [4]),
        ("Large payment → Controller + CFO", "payment", 500000, [4, 5]),
        ("Large manual journal → Controller", "journal_entry", 100000, [4]),
        ("Large sales order → Senior Accountant", "sales_order", 250000, [3]),
    ]:
        ApprovalRule.objects.create(company=company, name=name, document_type=dtype, min_amount=amount * k,
                                    approver_levels=levels)
    for name, days in [("Immediate", 0), ("Net 15", 15), ("Net 30", 30), ("Net 60", 60), ("Net 90", 90)]:
        PaymentTerm.objects.get_or_create(company=company, name=name, defaults={"days": days})
    Warehouse.objects.get_or_create(company=company, code="MAIN", defaults={"name": "Main Warehouse"})
    for name, method in [("Merchandise (FIFO)", "fifo"), ("Merchandise (Average cost)", "average")]:
        ProductCategory.objects.get_or_create(company=company, name=name, defaults=dict(
            cost_method=method, inventory_account=acc["110301"], cogs_account=acc["510101"],
            income_account=acc["410101"], expense_account=acc["520301"]))
    ProductCategory.objects.get_or_create(company=company, name="Raw Materials", defaults=dict(
        cost_method="average", inventory_account=acc["110302"], cogs_account=acc["510101"],
        income_account=acc["410101"], expense_account=acc["520301"]))
    ProductCategory.objects.get_or_create(company=company, name="Services", defaults=dict(
        cost_method="average", income_account=acc["410102"], expense_account=acc["520303"]))
    from apps.assets.services import ensure_default_categories
    ensure_default_categories(company, acc)
    create_fiscal_year(company, year)
    return acc, J, taxes
