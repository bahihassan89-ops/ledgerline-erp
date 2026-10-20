"""Create a fully configured company:  python manage.py setup_company --name "Acme LLC" --code acme --currency SAR --vat 15"""
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

from ..coa import CHART

CURRENCIES = [("USD", "US Dollar", "$", 2), ("EUR", "Euro", "€", 2), ("GBP", "Pound Sterling", "£", 2),
              ("SAR", "Saudi Riyal", "SR", 2), ("AED", "UAE Dirham", "AED", 2), ("QAR", "Qatari Riyal", "QR", 2),
              ("KWD", "Kuwaiti Dinar", "KD", 3), ("BHD", "Bahraini Dinar", "BD", 3), ("OMR", "Omani Rial", "OMR", 3),
              ("JOD", "Jordanian Dinar", "JD", 3), ("EGP", "Egyptian Pound", "E£", 2)]


def ensure_currencies():
    for code, name, sym, dec in CURRENCIES:
        Currency.objects.get_or_create(code=code, defaults={"name": name, "symbol": sym, "decimals": dec})


def build_chart(company):
    by_code = {}

    def make(node, parent, cls):
        code, name, typ = node[0], node[1], node[2]
        rest = node[3:]
        if parent is None:
            cls, rest = rest[0], rest[1:]
        children = rest[-1] if rest and isinstance(rest[-1], list) else []
        flags = [f for f in rest if isinstance(f, str)]
        a = Account(company=company, code=code, name=name, parent=parent, account_class=cls, account_type=typ,
                    is_control="control" in flags)
        a.clean()
        a.save()
        by_code[code] = a
        for ch in children:
            make(ch, a, cls)

    for root in CHART:
        make(root, None, None)
    return by_code


class Command(BaseCommand):
    help = "Create a company with chart of accounts, journals, taxes, roles and the current fiscal year"

    def add_arguments(self, p):
        p.add_argument("--name", required=True)
        p.add_argument("--code", required=True)
        p.add_argument("--currency", default="USD")
        p.add_argument("--vat", default="15", help="Standard VAT rate in percent (0 to skip)")
        p.add_argument("--fy-start-month", type=int, default=1)
        p.add_argument("--year", type=int, default=None)

    @transaction.atomic
    def handle(self, *a, **o):
        ensure_currencies()
        if Company.objects.filter(code=o["code"]).exists():
            raise CommandError("A company with this code already exists.")
        if not Currency.objects.filter(code=o["currency"]).exists():
            raise CommandError(f"Unknown currency {o['currency']}")
        company = Company.objects.create(name=o["name"], legal_name=o["name"], code=o["code"],
                                         base_currency_id=o["currency"], fiscal_year_start_month=o["fy_start_month"])
        setup(company, Decimal(o["vat"]), o["year"] or timezone.localdate().year)
        self.stdout.write(self.style.SUCCESS(f"Company '{company.name}' created (id={company.pk})."))


def setup(company, vat, year):
    acc = build_chart(company)
    J = {}
    for code, name, typ, default, manual in [
        ("SAL", "Customer Invoices", "sale", None, False),
        ("PUR", "Vendor Bills", "purchase", None, False),
        ("BNK", "Main Bank", "bank", "110110", True),
        ("BNK2", "Foreign Currency Bank", "bank", "110111", True),
        ("CSH", "Cash Box", "cash", "110101", True),
        ("STK", "Inventory Valuation", "inventory", None, False),
        ("GEN", "General Journal", "general", None, True),
        ("OPN", "Opening Balances", "general", None, True),
        ("CLS", "Year-End Closing", "general", None, False),
    ]:
        J[code] = Journal.objects.create(company=company, code=code, name=name, type=typ, allow_manual=manual,
                                         default_account=acc.get(default) if default else None)
    taxes = {}
    if vat:
        taxes["S"] = Tax.objects.create(company=company, code=f"VAT{vat:g}-S", name=f"VAT {vat:g}% (sales)",
                                        scope="sale", rate=vat, account=acc["210301"])
        taxes["P"] = Tax.objects.create(company=company, code=f"VAT{vat:g}-P", name=f"VAT {vat:g}% (purchases)",
                                        scope="purchase", rate=vat, account=acc["110403"])
        Tax.objects.create(company=company, code="VAT0-S", name="Zero-rated (sales)", scope="sale", rate=0,
                           account=acc["210301"])
        Tax.objects.create(company=company, code="EXEMPT-P", name="Exempt (purchases)", scope="purchase", rate=0,
                           account=acc["110403"])
    AccountingSettings.objects.create(
        company=company, receivable_account=acc["110201"], payable_account=acc["210101"],
        grni_account=acc["210102"], price_variance_account=acc["510102"],
        inventory_adjustment_account=acc["510103"], fx_gain_account=acc["420101"], fx_loss_account=acc["530101"],
        retained_earnings_account=acc["320101"], sales_journal=J["SAL"], purchase_journal=J["PUR"],
        inventory_journal=J["STK"], general_journal=J["GEN"], closing_journal=J["CLS"])
    for name, level, perms, limit in DEFAULT_ROLES:
        Role.objects.create(company=company, name=name, level=level, permissions=sorted(set(perms)),
                            approval_limit=Decimal(limit) if limit is not None else None)
    for name, dtype, amount, levels in [
        ("PO above 50k → Controller", "purchase_order", 50000, [4]),
        ("PO above 250k → Controller + CFO", "purchase_order", 250000, [4, 5]),
        ("Vendor bill above 250k → Controller", "vendor_bill", 250000, [4]),
        ("Payment above 100k → Controller", "payment", 100000, [4]),
        ("Payment above 500k → Controller + CFO", "payment", 500000, [4, 5]),
        ("Manual journal above 100k → Controller", "journal_entry", 100000, [4]),
        ("Sales order above 250k → Senior Accountant", "sales_order", 250000, [3]),
    ]:
        ApprovalRule.objects.create(company=company, name=name, document_type=dtype, min_amount=amount,
                                    approver_levels=levels)
    for name, days in [("Immediate", 0), ("Net 15", 15), ("Net 30", 30), ("Net 60", 60), ("Net 90", 90)]:
        PaymentTerm.objects.create(company=company, name=name, days=days)
    Warehouse.objects.create(company=company, code="MAIN", name="Main Warehouse")
    for name, method in [("Merchandise (FIFO)", "fifo"), ("Merchandise (Average cost)", "average")]:
        ProductCategory.objects.create(company=company, name=name, cost_method=method,
                                       inventory_account=acc["110301"], cogs_account=acc["510101"],
                                       income_account=acc["410101"], expense_account=acc["520301"])
    ProductCategory.objects.create(company=company, name="Services", cost_method="average",
                                   income_account=acc["410102"], expense_account=acc["520303"])
    create_fiscal_year(company, year)
    return acc, J, taxes
