"""Seed realistic users/partners/products/transactions into an EXISTING company.

Unlike seed_demo / seed_demo_eg, this command never CREATES a company -- it looks up a Company
row that already exists (by `code`) and, only if that company's chart of accounts / fiscal year
is missing, sets it up by calling straight into `setup_company.setup()` (the exact same chart,
journals, default taxes and roles as a brand-new company gets -- never duplicated here). It then
adds a handful of users, partners and products, and posts a believable 1-3 month cycle of
transactions (opening balances, a purchase → receipt → bill → payment, a sales order → delivery →
invoice → receipt, an overdue invoice for aging, recurring rent/payroll/bank-charge lines) so the
company's reports actually show non-trivial, multi-category numbers.

    python manage.py seed_existing_company --company smoketest1
    python manage.py seed_existing_company --company smoketest2 --months 3
    python manage.py seed_existing_company --company smoketest1 --months 2 --force
"""
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.billing import services as billing
from apps.billing.models import Invoice, InvoiceLine, Payment
from apps.core import context
from apps.core.models import Company, CostCenter
from apps.iam.models import Membership, Role, User
from apps.inventory import services as stock
from apps.inventory.models import MoveType, Product, ProductCategory, Warehouse
from apps.ledger import services as gl
from apps.ledger.imports import opening_trial_balance
from apps.ledger.models import Account, FiscalYear, Journal, JournalEntry, Tax
from apps.ledger.services import create_fiscal_year
from apps.partners.models import Partner, PaymentTerm
from apps.purchasing import services as purchasing
from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine
from apps.sales import services as sales
from apps.sales.models import SalesOrder, SalesOrderLine

from .seed_demo import DEMO_PASSWORD, USERS
from .setup_company import setup as setup_company_coa

# Distinguishing "flavour" packs so smoketest1 and smoketest2 don't come out as copies of each
# other -- different partner names, different products, different prices and opening capital, so
# the two companies' trial balances / income statements are visibly different. Any company code
# that isn't listed here falls back to a generic pack derived from the company's name, so the
# command still works sensibly for other codes.
FLAVORS = {
    "smoketest1": dict(
        customers=[("Giza Hardware Retail", Decimal("600000")), ("Mansoura Builders Supply", Decimal("350000"))],
        vendors=[("Alexandria Steel Mills", "goods"), ("Nasr City Facilities Services", "services")],
        merch=dict(sku="FG-SHLF", name="Steel shelving unit", name_ar="رف صلب",
                   purchase_price=Decimal("260"), sale_price=Decimal("450")),
        addon=dict(sku="FG-BRK", name="Shelving bracket set", name_ar="طقم حوامل الرف",
                   purchase_price=Decimal("18"), sale_price=Decimal("35")),
        svc=dict(sku="SRV-DLV", name="Delivery & installation", name_ar="توصيل وتركيب", sale_price=Decimal("120")),
        capital=Decimal("1000000"),
    ),
    "smoketest2": dict(
        customers=[("Sharm Resorts Procurement", Decimal("900000")), ("Tanta Agro Processing Co.", Decimal("400000"))],
        vendors=[("10th of Ramadan Plastics Ltd", "goods"), ("Smart Office Supplies", "services")],
        merch=dict(sku="FG-CHR", name="Stackable plastic chair", name_ar="كرسي بلاستيك قابل للتكديس",
                   purchase_price=Decimal("90"), sale_price=Decimal("175")),
        addon=dict(sku="FG-TBL", name="Folding table", name_ar="طاولة قابلة للطي",
                   purchase_price=Decimal("140"), sale_price=Decimal("260")),
        svc=dict(sku="SRV-ASM", name="Assembly service", name_ar="خدمة تجميع", sale_price=Decimal("80")),
        capital=Decimal("1500000"),
    ),
}


def flavor_for(company):
    if company.code in FLAVORS:
        return FLAVORS[company.code]
    base = (company.name or company.code).split()[0]
    return dict(
        customers=[(f"{base} Retail Partner", Decimal("500000")), (f"{base} Regional Distributor", Decimal("300000"))],
        vendors=[(f"{base} Materials Supplier", "goods"), (f"{base} Services Contractor", "services")],
        merch=dict(sku="FG-GEN1", name=f"{base} finished good", name_ar="", purchase_price=Decimal("200"),
                   sale_price=Decimal("350")),
        addon=dict(sku="FG-GEN2", name=f"{base} accessory", name_ar="", purchase_price=Decimal("20"),
                   sale_price=Decimal("40")),
        svc=dict(sku="SRV-GEN1", name=f"{base} service", name_ar="", sale_price=Decimal("100")),
        capital=Decimal("800000"),
    )


class Command(BaseCommand):
    help = "Seed users, partners, products and a realistic transaction cycle into an EXISTING company."

    def add_arguments(self, p):
        p.add_argument("--company", required=True, help="Code of an existing Company row (not created here).")
        p.add_argument("--months", type=int, default=1, help="How many monthly transaction cycles to post (1-3).")
        p.add_argument("--force", action="store_true",
                       help="Seed anyway even if the company already has posted journal entries.")

    def handle(self, *a, **o):
        # Own context for the run: the acting user set below must not outlive the command.
        token = context.set_context(context.RequestContext())
        try:
            self.seed(o)
        finally:
            context.reset(token)

    @transaction.atomic
    def seed(self, o):
        code = o["company"]
        try:
            c = Company.objects.get(code=code)
        except Company.DoesNotExist:
            raise CommandError(
                f"No company with code '{code}'. This command targets an EXISTING company -- use "
                "setup_company (or seed_demo / seed_demo_eg) to create a new one first.")

        already_seeded = JournalEntry.objects.filter(company=c, state="posted").exists()
        if already_seeded and not o["force"]:
            raise CommandError(
                f"Company '{code}' already has posted journal entries -- refusing to pile more demo "
                "data onto a company that may have real activity. Pass --force to seed anyway.")

        months = max(1, min(3, o["months"] or 1))
        flavor = flavor_for(c)
        egypt = c.country == "EG"

        # --- Chart of accounts / journals / fiscal year: reuse setup_company's helper, never
        # duplicate chart-of-accounts logic here. Companies that already went through
        # `setup_company` (as smoketest1/smoketest2 did) already have all of this; this is a
        # safety net for a genuinely blank company.
        if not Account.objects.filter(company=c).exists():
            setup_company_coa(c, Decimal("14") if egypt else Decimal("15"), timezone.localdate().year, egypt=egypt)
            self.stdout.write("Chart of accounts, journals, taxes, roles and fiscal year created.")
        elif not FiscalYear.objects.filter(company=c).exists():
            create_fiscal_year(c, timezone.localdate().year)
            self.stdout.write("Fiscal year created.")

        # --- Users: same usernames/roles/password as seed_demo.py, reused across companies exactly
        # like seed_demo_eg.py does -- an existing user just gets an extra Membership + role here.
        users = {}
        for uname, role, first, last in USERS:
            u, _ = User.objects.get_or_create(username=uname, defaults={
                "email": f"{uname}@demo.local", "first_name": first, "last_name": last})
            if not u.has_usable_password():
                u.set_password(DEMO_PASSWORD)
                u.save()
            m, _ = Membership.objects.get_or_create(user=u, company=c)
            m.roles.set([Role.objects.get(company=c, name=role)])
            users[uname] = u
        cfo = users["cfo"]
        ctx = context.get()
        ctx.user, ctx.actor_label, ctx.company_id = cfo, "seed_existing_company", c.pk

        A = {x.code: x for x in Account.objects.filter(company=c)}
        J = {x.code: x for x in Journal.objects.filter(company=c)}
        T = {x.code: x for x in Tax.objects.filter(company=c)}
        wh = Warehouse.objects.get(company=c, code="MAIN")
        net30 = PaymentTerm.objects.get(company=c, name="Net 30")
        cats = {x.name: x for x in ProductCategory.objects.filter(company=c)}

        if egypt:
            vat_s, vat_p = T["VAT14-S"], T["VAT14-P"]
            wht_goods_p, wht_services_p = T.get("WHT1-SUP-P"), T.get("WHT3-SRV-P")
        else:
            vat_s = Tax.objects.filter(company=c, scope="sale", kind="vat", rate__gt=0).order_by("rate").first()
            vat_p = Tax.objects.filter(company=c, scope="purchase", kind="vat", rate__gt=0).order_by("rate").first()
            wht_goods_p = wht_services_p = None

        for cc_code, cc_name in [("ADM", "Administration"), ("SLS", "Sales"), ("OPS", "Operations")]:
            CostCenter.objects.get_or_create(company=c, code=cc_code, defaults={"name": cc_name})
        cc = {x.code: x for x in CostCenter.objects.filter(company=c)}

        currency = c.base_currency_id
        today = timezone.localdate()
        if already_seeded:
            # Top-up run: anchor strictly after the latest date already used by this company, never
            # recomputed from "today" -- a prior run may have used different `--months`, so the two
            # date ranges would otherwise overlap and collide on unique vendor-bill references.
            last_po = PurchaseOrder.objects.filter(company=c).order_by("-date").values_list("date", flat=True).first()
            last_so = SalesOrder.objects.filter(company=c).order_by("-date").values_list("date", flat=True).first()
            latest = max((d for d in (last_po, last_so) if d), default=today - timedelta(days=30 * months + 30))
            d0 = latest + timedelta(days=35)
        else:
            d0 = max(today.replace(month=1, day=1), today - timedelta(days=30 * months + 30))

        # --- Partners (customers + vendors), distinguished per-company by `flavor`
        cust = []
        for i, (name, limit) in enumerate(flavor["customers"], 1):
            p, _ = Partner.objects.get_or_create(company=c, code=f"C{i:03d}", defaults=dict(
                name=name, is_customer=True, payment_term=net30, credit_check=True, credit_limit=limit,
                country=c.country, withholding_tax=T.get("WHT1-SUP-S") if egypt else None))
            cust.append(p)
        vend = []
        for i, (name, kind) in enumerate(flavor["vendors"], 1):
            wht = wht_services_p if kind == "services" else wht_goods_p
            p, _ = Partner.objects.get_or_create(company=c, code=f"V{i:03d}", defaults=dict(
                name=name, is_vendor=True, payment_term=net30, country=c.country, withholding_tax=wht))
            vend.append(p)

        # --- Products: two sellable goods (so FIFO/average cost and two revenue lines show up) plus
        # one service, all distinguished per-company by `flavor`.
        m, a_, s_ = flavor["merch"], flavor["addon"], flavor["svc"]
        merch = Product.objects.get_or_create(company=c, sku=m["sku"], defaults=dict(
            name=m["name"], name_ar=m["name_ar"], category=cats["Merchandise (FIFO)"], sale_price=m["sale_price"],
            purchase_price=m["purchase_price"], sale_tax=vat_s, purchase_tax=vat_p, reorder_level=10))[0]
        addon = Product.objects.get_or_create(company=c, sku=a_["sku"], defaults=dict(
            name=a_["name"], name_ar=a_["name_ar"], category=cats["Merchandise (Average cost)"],
            sale_price=a_["sale_price"], purchase_price=a_["purchase_price"], sale_tax=vat_s, purchase_tax=vat_p,
            reorder_level=20))[0]
        svc = Product.objects.get_or_create(company=c, sku=s_["sku"], defaults=dict(
            name=s_["name"], name_ar=s_["name_ar"], type="service", category=cats["Services"],
            sale_price=s_["sale_price"], sale_tax=vat_s))[0]

        # --- Opening balances: paid-up capital in bank + petty cash, plus opening merchandise stock.
        # Only on the very first run against this company -- a --force top-up run (adding more
        # months to a company that already has data) must never re-inject paid-up capital or
        # opening stock a second time, since posted entries can't be deleted to undo that mistake.
        if not already_seeded:
            opening_cash = Decimal("40000")
            opening_bank = flavor["capital"] - opening_cash
            opening_trial_balance(c, d0, [
                {"account_code": "110110", "debit": str(opening_bank)},
                {"account_code": "110101", "debit": str(opening_cash)},
                {"account_code": "310101", "credit": str(flavor["capital"])},
            ], cfo)
            stock.receive(c, merch, wh, 60, m["purchase_price"], d0, cfo, counter_account=A["320102"],
                          move_type=MoveType.OPENING, reference="Opening stock")
            stock.receive(c, addon, wh, 150, a_["purchase_price"], d0, cfo, counter_account=A["320102"],
                          move_type=MoveType.OPENING, reference="Opening stock")

        # --- Monthly transaction cycles: purchase → receipt → bill → payment, sale → delivery →
        # invoice → receipt, plus rent / payroll / bank-charge lines so the income statement spans
        # several account categories (COGS, salaries, rent, bank charges, goods + service revenue).
        for month_idx in range(months):
            cyc = d0 + timedelta(days=30 * month_idx)
            last_cycle = month_idx == months - 1

            po = PurchaseOrder.objects.create(company=c, partner=vend[0], date=cyc + timedelta(days=2),
                                              currency_id=currency, warehouse=wh, payment_term=net30,
                                              created_by=users["clerk"])
            PurchaseOrderLine.objects.create(order=po, product=merch, quantity=140, unit_price=m["purchase_price"],
                                             tax=vat_p)
            PurchaseOrderLine.objects.create(order=po, product=addon, quantity=260, unit_price=a_["purchase_price"],
                                             tax=vat_p)
            purchasing.confirm(po, cfo)
            purchasing.receive(po, cfo, on_date=cyc + timedelta(days=4))
            bill = billing.post_invoice(purchasing.create_bill(po, cfo, cyc + timedelta(days=5),
                                                                f"{vend[0].code}-{cyc.isoformat()}"), cfo)
            if egypt and month_idx == 0:
                pay = Payment.objects.create(company=c, direction="outbound", partner=vend[0], journal=J["BNK"],
                                             date=cyc + timedelta(days=20), amount=bill.amount_total,
                                             currency_id=currency, method="cheque", cheque_number=f"00{month_idx + 1}451",
                                             cheque_bank="Banque Misr", cheque_due_date=cyc + timedelta(days=35))
            else:
                pay = Payment.objects.create(company=c, direction="outbound", partner=vend[0], journal=J["BNK"],
                                             date=cyc + timedelta(days=20), amount=bill.amount_total,
                                             currency_id=currency)
            billing.post_payment(pay, cfo, [(Invoice.objects.get(pk=bill.pk), bill.amount_total)])

            so = SalesOrder.objects.create(company=c, partner=cust[0], date=cyc + timedelta(days=8),
                                           currency_id=currency, warehouse=wh, payment_term=net30,
                                           created_by=users["clerk"])
            SalesOrderLine.objects.create(order=so, product=merch, quantity=90, unit_price=m["sale_price"],
                                          tax=vat_s)
            SalesOrderLine.objects.create(order=so, product=addon, quantity=180, unit_price=a_["sale_price"],
                                          discount_pct=5, tax=vat_s)
            SalesOrderLine.objects.create(order=so, product=svc, quantity=60, unit_price=s_["sale_price"], tax=vat_s)
            sales.confirm(so, cfo)
            sales.deliver(so, cfo, on_date=cyc + timedelta(days=10))
            inv = billing.post_invoice(sales.create_invoice(so, cfo, cyc + timedelta(days=10)), cfo)
            rc_amount = (inv.amount_total * Decimal("0.6")).quantize(Decimal("0.01"))
            rc = Payment.objects.create(company=c, direction="inbound", partner=cust[0], journal=J["BNK"],
                                        date=cyc + timedelta(days=24), amount=rc_amount, currency_id=currency,
                                        reference=f"RCPT-{cyc.isoformat()}")
            billing.post_payment(rc, cfo, [(Invoice.objects.get(pk=inv.pk), rc_amount)])

            # Second customer, smaller order, paid in full -- so both customers have ledger history.
            so2 = SalesOrder.objects.create(company=c, partner=cust[1], date=cyc + timedelta(days=9),
                                            currency_id=currency, warehouse=wh, payment_term=net30,
                                            created_by=users["clerk"])
            SalesOrderLine.objects.create(order=so2, product=merch, quantity=40, unit_price=m["sale_price"],
                                          tax=vat_s)
            sales.confirm(so2, cfo)
            sales.deliver(so2, cfo, on_date=cyc + timedelta(days=11))
            inv2 = billing.post_invoice(sales.create_invoice(so2, cfo, cyc + timedelta(days=11)), cfo)
            rc2 = Payment.objects.create(company=c, direction="inbound", partner=cust[1], journal=J["BNK"],
                                         date=cyc + timedelta(days=22), amount=inv2.amount_total,
                                         currency_id=currency)
            billing.post_payment(rc2, cfo, [(Invoice.objects.get(pk=inv2.pk), inv2.amount_total)])

            # Rent (services vendor, 3% WHT) + salaries accrual + bank charges -- spreads expenses
            # across several income-statement categories besides COGS.
            rent = Invoice.objects.create(company=c, move_type="in_invoice", partner=vend[1], currency_id=currency,
                                          date=cyc + timedelta(days=1), reference=f"RENT-{cyc.isoformat()}")
            InvoiceLine.objects.create(invoice=rent, description="Office & warehouse rent", account=A["520201"],
                                       quantity=1, unit_price=Decimal("12000"), tax=vat_p,
                                       withholding_tax=wht_services_p, cost_center=cc["ADM"])
            billing.post_invoice(rent, cfo)

            payroll = gl.create_entry(c, J["GEN"], cyc + timedelta(days=28), [
                gl.LineSpec(account=A["520101"], debit=Decimal("22000"), description="Monthly salaries accrual",
                           cost_center=cc["ADM"]),
                gl.LineSpec(account=A["210201"], credit=Decimal("22000"), description="Monthly salaries accrual"),
            ], user=users["accountant"], reference=f"PAYROLL-{cyc.isoformat()}", narration="Monthly payroll accrual")
            gl.post_entry(payroll, cfo)

            charges = gl.create_entry(c, J["GEN"], cyc + timedelta(days=29), [
                gl.LineSpec(account=A["520306"], debit=Decimal("65"), description="Bank charges"),
                gl.LineSpec(account=A["110110"], credit=Decimal("65"), description="Bank charges"),
            ], user=users["clerk"], reference=f"BNKCHG-{cyc.isoformat()}", narration="Monthly bank charges")
            gl.post_entry(charges, cfo)

            if last_cycle:
                # Overdue, unpaid invoice for the aging report -- due date already in the past.
                overdue = Invoice.objects.create(company=c, move_type="out_invoice", partner=cust[1],
                                                 currency_id=currency, date=cyc + timedelta(days=2),
                                                 due_date=cyc + timedelta(days=9), created_by=users["clerk"])
                InvoiceLine.objects.create(invoice=overdue, product=svc, description="Overdue consulting services",
                                           account=A["410102"], quantity=15, unit_price=s_["sale_price"], tax=vat_s)
                billing.post_invoice(overdue, cfo)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded '{c.name}' ({c.code}) with {months} month(s) of transactions. "
            f"Users: {', '.join(u for u, *_ in USERS)} — password: {DEMO_PASSWORD}"))
