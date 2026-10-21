"""Demo company with one user per role and a month of real transactions posted through the services.

    python manage.py seed_demo            (password for every demo user is printed at the end)
"""
from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.billing import services as billing
from apps.billing.models import Invoice, InvoiceLine, Payment
from apps.core import context
from apps.core.models import Company, CostCenter, ExchangeRate
from apps.iam.models import Membership, Role, User
from apps.inventory import services as stock
from apps.inventory.models import MoveType, Product, ProductCategory, Warehouse
from apps.ledger import services as gl
from apps.ledger.models import Account, Journal, Tax
from apps.partners.models import Partner, PaymentTerm
from apps.purchasing import services as purchasing
from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine
from apps.sales import services as sales
from apps.sales.models import SalesOrder, SalesOrderLine

DEMO_PASSWORD = "Ledger!Demo2026"
USERS = [
    ("admin", "System Administrator", "Sara", "Admin"),
    ("clerk", "Accounting Clerk", "Omar", "Clerk"),
    ("accountant", "Accountant", "Lina", "Accountant"),
    ("senior", "Senior Accountant", "Yusuf", "Senior"),
    ("controller", "Financial Controller", "Huda", "Controller"),
    ("cfo", "CFO / Finance Director", "Karim", "CFO"),
    ("auditor", "Auditor (read-only)", "Noor", "Auditor"),
]


class Command(BaseCommand):
    help = "Create a demo company, users for every role, and sample transactions"

    def add_arguments(self, p):
        p.add_argument("--code", default="demo")

    def handle(self, *a, **o):
        # Own context for the run: the acting user set below must not outlive the command.
        token = context.set_context(context.RequestContext())
        try:
            self.seed(o)
        finally:
            context.reset(token)

    @transaction.atomic
    def seed(self, o):
        code = o["code"]
        if Company.objects.filter(code=code).exists():
            self.stdout.write("Demo company already exists; nothing to do.")
            return
        call_command("setup_company", name="Demo Trading Co.", code=code, currency="USD", vat="15")
        c = Company.objects.get(code=code)
        c.tax_number = "300000000000003"
        c.address = "King Fahd Road, Riyadh"
        c.save()

        if not User.objects.filter(username="root").exists():
            User.objects.create_superuser("root", "root@example.com", DEMO_PASSWORD)
        users = {}
        for uname, role, first, last in USERS:
            u, _ = User.objects.get_or_create(username=uname, defaults={
                "email": f"{uname}@demo.local", "first_name": first, "last_name": last})
            u.set_password(DEMO_PASSWORD)
            u.save()
            m, _ = Membership.objects.get_or_create(user=u, company=c, defaults={"is_default": True})
            m.roles.set([Role.objects.get(company=c, name=role)])
            users[uname] = u
        acc_user = users["cfo"]  # post demo history as the CFO (has every accounting right)
        ctx = context.get()
        ctx.user, ctx.actor_label, ctx.company_id = acc_user, "seed_demo", c.pk

        A = {a.code: a for a in Account.objects.filter(company=c)}
        J = {j.code: j for j in Journal.objects.filter(company=c)}
        vat_s = Tax.objects.get(company=c, code="VAT15-S")
        vat_p = Tax.objects.get(company=c, code="VAT15-P")
        net30 = PaymentTerm.objects.get(company=c, name="Net 30")
        wh = Warehouse.objects.get(company=c, code="MAIN")
        fifo = ProductCategory.objects.get(company=c, cost_method="fifo")
        avg = ProductCategory.objects.get(company=c, name__startswith="Merchandise (Average")
        svc = ProductCategory.objects.get(company=c, name="Services")
        for code_, name in [("ADM", "Administration"), ("SLS", "Sales Department"), ("OPS", "Operations")]:
            CostCenter.objects.create(company=c, code=code_, name=name)

        today = timezone.localdate()
        d0 = max(today.replace(month=1, day=1), today - timedelta(days=45))
        ExchangeRate.objects.create(company=c, currency_id="EUR", date=d0, rate=Decimal("1.08"))
        ExchangeRate.objects.create(company=c, currency_id="EUR", date=d0 + timedelta(days=20), rate=Decimal("1.10"))

        cust = [Partner.objects.create(company=c, code=f"C{i:03d}", name=n, is_customer=True, payment_term=net30,
                                       email=f"ap@{n.split()[0].lower()}.example", credit_limit=Decimal(lim),
                                       credit_check=True, country=cc)
                for i, (n, lim, cc) in enumerate([("Al Noor Retail", "150000", "SA"), ("Gulf Hospitality Group",
                                                  "80000", "AE"), ("Berlin Imports GmbH", "60000", "DE")], 1)]
        vend = [Partner.objects.create(company=c, code=f"V{i:03d}", name=n, is_vendor=True, payment_term=net30,
                                       country=cc)
                for i, (n, cc) in enumerate([("Prime Electronics Supply", "CN"), ("Office World", "SA"),
                                             ("Desert Logistics", "SA")], 1)]
        p_laptop = Product.objects.create(company=c, sku="LAP-001", name="Business Laptop 14\"", category=fifo,
                                          sale_price=1200, purchase_price=800, sale_tax=vat_s, purchase_tax=vat_p,
                                          reorder_level=5)
        p_monitor = Product.objects.create(company=c, sku="MON-027", name="27\" Monitor", category=avg,
                                           sale_price=320, purchase_price=190, sale_tax=vat_s, purchase_tax=vat_p,
                                           reorder_level=10)
        p_install = Product.objects.create(company=c, sku="SRV-INST", name="Installation & Setup", type="service",
                                           category=svc, sale_price=150, sale_tax=vat_s)

        # Opening balances: capital in the bank, opening stock against opening equity
        e = gl.create_entry(c, J["OPN"], d0, [
            gl.LineSpec(account=A["110110"], debit=Decimal("250000"), description="Opening bank balance"),
            gl.LineSpec(account=A["110101"], debit=Decimal("5000"), description="Opening cash"),
            gl.LineSpec(account=A["310101"], credit=Decimal("255000"), description="Paid-up capital"),
        ], user=acc_user, reference="Opening balances", narration="Opening balances")
        gl.post_entry(e, acc_user)
        stock.receive(c, p_monitor, wh, 20, 180, d0, acc_user, counter_account=A["320102"],
                      move_type=MoveType.OPENING, reference="Opening stock")

        # Purchase → receipt → bill → payment
        po = PurchaseOrder.objects.create(company=c, partner=vend[0], date=d0 + timedelta(days=2), currency_id="USD",
                                          warehouse=wh, payment_term=net30, created_by=users["clerk"])
        PurchaseOrderLine.objects.create(order=po, product=p_laptop, quantity=20, unit_price=800, tax=vat_p)
        PurchaseOrderLine.objects.create(order=po, product=p_monitor, quantity=30, unit_price=195, tax=vat_p)
        purchasing.confirm(po, acc_user)
        purchasing.receive(po, acc_user, on_date=d0 + timedelta(days=5))
        bill = purchasing.create_bill(po, acc_user, on_date=d0 + timedelta(days=6), vendor_reference="PES-88121")
        billing.post_invoice(bill, acc_user)
        pay = Payment.objects.create(company=c, direction="outbound", partner=vend[0], journal=J["BNK"],
                                     date=d0 + timedelta(days=20), amount=bill.amount_total, currency_id="USD")
        billing.post_payment(pay, acc_user, allocations=[(Invoice.objects.get(pk=bill.pk), bill.amount_total)])

        # Second laptop lot at a higher price (shows FIFO layers)
        po2 = PurchaseOrder.objects.create(company=c, partner=vend[0], date=d0 + timedelta(days=15),
                                           currency_id="USD", warehouse=wh, created_by=users["clerk"])
        PurchaseOrderLine.objects.create(order=po2, product=p_laptop, quantity=10, unit_price=850, tax=vat_p)
        purchasing.confirm(po2, acc_user)
        purchasing.receive(po2, acc_user, on_date=d0 + timedelta(days=18))

        # Sales: order → delivery → invoice → partial receipt
        so = SalesOrder.objects.create(company=c, partner=cust[0], date=d0 + timedelta(days=10), currency_id="USD",
                                       warehouse=wh, payment_term=net30, created_by=users["clerk"])
        SalesOrderLine.objects.create(order=so, product=p_laptop, quantity=25, unit_price=1200, tax=vat_s)
        SalesOrderLine.objects.create(order=so, product=p_monitor, quantity=15, unit_price=320, discount_pct=5,
                                      tax=vat_s)
        SalesOrderLine.objects.create(order=so, product=p_install, quantity=10, unit_price=150, tax=vat_s)
        sales.confirm(so, acc_user)
        sales.deliver(so, acc_user, on_date=d0 + timedelta(days=21))
        inv = sales.create_invoice(so, acc_user, on_date=d0 + timedelta(days=21))
        billing.post_invoice(inv, acc_user)
        rc = Payment.objects.create(company=c, direction="inbound", partner=cust[0], journal=J["BNK"],
                                    date=d0 + timedelta(days=30), amount=Decimal("20000"), currency_id="USD",
                                    reference="TRF-552210")
        billing.post_payment(rc, acc_user, allocations=[(Invoice.objects.get(pk=inv.pk), Decimal("20000"))])

        # EUR invoice paid at a different rate → realised FX gain
        e_inv = Invoice.objects.create(company=c, move_type="out_invoice", partner=cust[2], currency_id="EUR",
                                       date=d0 + timedelta(days=12), payment_term=net30, created_by=users["clerk"])
        InvoiceLine.objects.create(invoice=e_inv, product=p_install, description="Remote configuration services",
                                   account=A["410102"], quantity=20, unit_price=140)
        billing.post_invoice(e_inv, acc_user)
        e_pay = Payment.objects.create(company=c, direction="inbound", partner=cust[2], journal=J["BNK2"],
                                       date=d0 + timedelta(days=25), amount=Decimal("2800"), currency_id="EUR")
        billing.post_payment(e_pay, acc_user, allocations=[(Invoice.objects.get(pk=e_inv.pk), Decimal("2800"))])

        # Overdue invoice for the aging report
        old = Invoice.objects.create(company=c, move_type="out_invoice", partner=cust[1], currency_id="USD",
                                     date=d0, due_date=d0 + timedelta(days=7), created_by=users["clerk"])
        InvoiceLine.objects.create(invoice=old, product=p_install, description="Event AV setup",
                                   account=A["410102"], quantity=12, unit_price=150, tax=vat_s)
        billing.post_invoice(old, acc_user)

        # Expense bill without PO (rent) + manual accrual
        rent = Invoice.objects.create(company=c, move_type="in_invoice", partner=vend[1], currency_id="USD",
                                      date=d0 + timedelta(days=1), reference="OW-2026-114", created_by=users["clerk"])
        InvoiceLine.objects.create(invoice=rent, description="Office rent", account=A["520201"], quantity=1,
                                   unit_price=6000, tax=vat_p, cost_center=CostCenter.objects.get(company=c, code="ADM"))
        billing.post_invoice(rent, acc_user)
        acc = gl.create_entry(c, J["GEN"], d0 + timedelta(days=28), [
            gl.LineSpec(account=A["520101"], debit=Decimal("18500"), description="Salaries accrual",
                        cost_center=CostCenter.objects.get(company=c, code="ADM")),
            gl.LineSpec(account=A["210201"], credit=Decimal("18500"), description="Salaries accrual"),
        ], user=users["accountant"], reference="ACCR-SAL", narration="Monthly salaries accrual")
        gl.post_entry(acc, acc_user)
        # A draft left for the clerk/accountant to see
        gl.create_entry(c, J["GEN"], today, [
            gl.LineSpec(account=A["520306"], debit=Decimal("45"), description="Bank charges"),
            gl.LineSpec(account=A["110110"], credit=Decimal("45"), description="Bank charges"),
        ], user=users["clerk"], reference="BNK-CHG", narration="Monthly bank charges (draft)")

        self.stdout.write(self.style.SUCCESS(
            f"Demo ready. Users: root (superuser), {', '.join(u for u, *_ in USERS)} — password: {DEMO_PASSWORD}\n"
            "Controller and CFO must enrol an authenticator app (MFA) at first login."))
