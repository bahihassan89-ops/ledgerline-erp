"""Egyptian demo company exercising every module: VAT 14%, withholding tax, cheques, bank
reconciliation, fixed assets, production, landed costs, budgets, recurring entries.

    python manage.py seed_demo_eg      (run seed_demo first to create the demo users)
"""
from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.assets import services as fa
from apps.assets.models import Asset, AssetCategory
from apps.banking import services as bank
from apps.billing import services as billing
from apps.billing.models import Invoice, InvoiceLine, Payment
from apps.core import context
from apps.core.models import Company, CostCenter, ExchangeRate
from apps.einvoice.models import EtaSettings
from apps.iam.models import Membership, Role, User
from apps.inventory import services as stock
from apps.inventory.models import LandedCost, MoveType, Product, ProductCategory, Warehouse
from apps.ledger import services as gl
from apps.ledger.imports import opening_trial_balance
from apps.ledger.models import Account, FiscalYear, Journal, Tax
from apps.manufacturing import services as mrp
from apps.manufacturing.models import Bom, BomComponent, BomOperation, ManufacturingOrder, WorkCenter
from apps.partners.models import Partner, PaymentTerm
from apps.planning.models import Budget, RecurringEntry
from apps.planning.services import run_due, set_budget_lines
from apps.purchasing import services as purchasing
from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine
from apps.sales import services as sales
from apps.sales.models import SalesOrder, SalesOrderLine

from .seed_demo import DEMO_PASSWORD, USERS


class Command(BaseCommand):
    help = "Create the Egyptian demo company"

    @transaction.atomic
    def handle(self, *a, **o):
        if Company.objects.filter(code="demo-eg").exists():
            self.stdout.write("Egyptian demo already exists.")
            return
        call_command("setup_company", name="Nile Industries SAE", name_ar="النيل للصناعات ش.م.م", code="demo-eg",
                     country="EG")
        c = Company.objects.get(code="demo-eg")
        c.tax_number, c.registration_number, c.tax_office = "123456789", "CR-98765", "مأمورية ضرائب الشركات المساهمة"
        c.address = "15 Mostafa El Nahas St., Nasr City, Cairo"
        c.save()
        EtaSettings.objects.create(company=c, enabled=False, environment="preprod", taxpayer_activity_code="2599",
                                   governate="Cairo", region_city="Nasr City", street="Mostafa El Nahas",
                                   building_number="15", signer_mode="unsigned_preprod")
        users = {}
        for uname, role, first, last in USERS:
            u, _ = User.objects.get_or_create(username=uname, defaults={"email": f"{uname}@demo.local",
                                                                        "first_name": first, "last_name": last})
            if not u.has_usable_password():
                u.set_password(DEMO_PASSWORD)
                u.save()
            m, _ = Membership.objects.get_or_create(user=u, company=c)
            m.roles.set([Role.objects.get(company=c, name=role)])
            users[uname] = u
        cfo = users["cfo"]
        ctx = context.get()
        ctx.user, ctx.actor_label, ctx.company_id = cfo, "seed_demo_eg", c.pk

        A = {x.code: x for x in Account.objects.filter(company=c)}
        J = {x.code: x for x in Journal.objects.filter(company=c)}
        T = {x.code: x for x in Tax.objects.filter(company=c)}
        wh = Warehouse.objects.get(company=c, code="MAIN")
        net30 = PaymentTerm.objects.get(company=c, name="Net 30")
        cats = {x.name: x for x in ProductCategory.objects.filter(company=c)}
        for code, name in [("ADM", "الإدارة / Administration"), ("FAC", "المصنع / Factory"),
                           ("SLS", "المبيعات / Sales")]:
            CostCenter.objects.create(company=c, code=code, name=name)
        cc = {x.code: x for x in CostCenter.objects.filter(company=c)}
        today = timezone.localdate()
        d0 = max(today.replace(month=1, day=1), today - timedelta(days=60))
        ExchangeRate.objects.create(company=c, currency_id="USD", date=d0, rate=Decimal("48.50"))
        ExchangeRate.objects.create(company=c, currency_id="USD", date=d0 + timedelta(days=30), rate=Decimal("49.20"))

        opening_trial_balance(c, d0, [
            {"account_code": "110110", "debit": "2500000"}, {"account_code": "110101", "debit": "50000"},
            {"account_code": "310101", "credit": "2000000"}, {"account_code": "320101", "credit": "550000"},
        ], cfo)

        cust = Partner.objects.create(company=c, code="C001", name="Cairo Furniture Retail", name_ar="القاهرة لتجارة الأثاث",
                                      is_customer=True, tax_number="200300400", governate="Cairo",
                                      region_city="Heliopolis", street="El Merghany", building_number="40",
                                      payment_term=net30, credit_check=True, credit_limit=Decimal("1500000"),
                                      withholding_tax=T["WHT1-SUP-S"])
        cust2 = Partner.objects.create(company=c, code="C002", name="Alexandria Offices Co.", is_customer=True,
                                       tax_number="300400500", governate="Alexandria", region_city="Smouha",
                                       street="Victor Emmanuel", building_number="8", payment_term=net30)
        steel_v = Partner.objects.create(company=c, code="V001", name="Egyptian Steel Supplies",
                                         name_ar="المصرية لتوريد الصلب", is_vendor=True, tax_number="400500600",
                                         payment_term=net30, withholding_tax=T["WHT1-SUP-P"])
        maint_v = Partner.objects.create(company=c, code="V002", name="Delta Maintenance Services", is_vendor=True,
                                         tax_number="500600700", withholding_tax=T["WHT3-SRV-P"])
        customs_v = Partner.objects.create(company=c, code="V003", name="Port Said Clearing Agent", is_vendor=True,
                                           tax_number="600700800")
        imp_v = Partner.objects.create(company=c, code="V004", name="Shanghai Fittings Ltd", is_vendor=True,
                                       country="CN", currency_id="USD")

        steel = Product.objects.create(company=c, sku="RM-STEEL", name="Steel sheet 1mm", name_ar="لوح صلب 1 مم",
                                       category=cats["Raw Materials"], uom="Sheet", purchase_price=1200,
                                       purchase_tax=T["VAT14-P"])
        fittings = Product.objects.create(company=c, sku="RM-FIT", name="Hinges & fittings set",
                                          category=cats["Raw Materials"], uom="Set", purchase_price=3)
        cabinet = Product.objects.create(company=c, sku="FG-CAB", name="Steel office cabinet",
                                         name_ar="دولاب مكتب صاج", category=cats["Merchandise (FIFO)"],
                                         sale_price=6500, sale_tax=T["VAT14-S"], eta_item_code="EG-123456789-CAB01")
        install = Product.objects.create(company=c, sku="SRV-INST", name="Installation service",
                                         name_ar="خدمة تركيب", type="service", category=cats["Services"],
                                         sale_price=500, sale_tax=T["VAT14-S"], eta_item_code="EG-123456789-SRV01")

        # Purchase steel (1% withholding on supplies), receive, bill, pay by cheque
        po = PurchaseOrder.objects.create(company=c, partner=steel_v, date=d0 + timedelta(days=2), currency_id="EGP",
                                          warehouse=wh, payment_term=net30, created_by=users["clerk"])
        PurchaseOrderLine.objects.create(order=po, product=steel, quantity=200, unit_price=1200, tax=T["VAT14-P"])
        purchasing.confirm(po, cfo)
        purchasing.receive(po, cfo, on_date=d0 + timedelta(days=4))
        b = billing.post_invoice(purchasing.create_bill(po, cfo, d0 + timedelta(days=5), "ESS-2026-0412"), cfo)
        chq = Payment.objects.create(company=c, direction="outbound", partner=steel_v, journal=J["BNK"],
                                     date=d0 + timedelta(days=20), amount=b.amount_total, currency_id="EGP",
                                     method="cheque", cheque_number="004512", cheque_bank="Banque Misr",
                                     cheque_due_date=d0 + timedelta(days=35))
        billing.post_payment(chq, cfo, [(Invoice.objects.get(pk=b.pk), b.amount_total)])

        # Imported fittings in USD + customs clearing as landed cost
        po2 = PurchaseOrder.objects.create(company=c, partner=imp_v, date=d0 + timedelta(days=3), currency_id="USD",
                                           warehouse=wh)
        PurchaseOrderLine.objects.create(order=po2, product=fittings, quantity=500, unit_price=3)
        purchasing.confirm(po2, cfo)
        moves = purchasing.receive(po2, cfo, on_date=d0 + timedelta(days=12))
        cb = Invoice.objects.create(company=c, move_type="in_invoice", partner=customs_v, currency_id="EGP",
                                    date=d0 + timedelta(days=13), reference="PSC-7781")
        InvoiceLine.objects.create(invoice=cb, description="Customs duty and clearing — fittings",
                                   account=A["210104"], quantity=1, unit_price=9000, tax=T["VAT14-P"])
        billing.post_invoice(cb, cfo)
        lc = LandedCost.objects.create(company=c, date=d0 + timedelta(days=13), description="Customs PSC-7781",
                                       amount=Decimal("9000"))
        lc.moves.set(moves)
        stock.post_landed_cost(lc, cfo)

        # Production: 40 cabinets
        wc = WorkCenter.objects.create(company=c, code="FAB", name="Fabrication line", labour_rate=Decimal("150"),
                                       overhead_rate=Decimal("90"), cost_center=cc["FAC"])
        bom = Bom.objects.create(company=c, code="BOM-CAB-01", product=cabinet, quantity=1)
        BomComponent.objects.create(bom=bom, product=steel, quantity=Decimal("3"), scrap_pct=Decimal("2"))
        BomComponent.objects.create(bom=bom, product=fittings, quantity=Decimal("4"))
        BomOperation.objects.create(bom=bom, name="Cut, bend & weld", work_center=wc, minutes=Decimal("90"))
        BomOperation.objects.create(bom=bom, name="Paint & assemble", work_center=wc, minutes=Decimal("45"))
        mo = ManufacturingOrder.objects.create(company=c, product=cabinet, bom=bom, quantity=40, warehouse=wh,
                                               planned_date=d0 + timedelta(days=15))
        mrp.confirm(mo, cfo)
        mrp.consume(mo, cfo, on_date=d0 + timedelta(days=15))
        ops = list(mo.operations.all())
        mrp.record_operations(mo, cfo, {ops[0].pk: 3700, ops[1].pk: 1750}, on_date=d0 + timedelta(days=18))
        mrp.produce(mo, cfo, 39, scrap_qty=1, on_date=d0 + timedelta(days=19), finish=True)
        # actual factory wages for the period (absorbed vs actual → under/over absorption)
        e = gl.create_entry(c, J["GEN"], d0 + timedelta(days=28), [
            gl.LineSpec(account=A["510201"], debit=Decimal("15000"), cost_center=cc["FAC"]),
            gl.LineSpec(account=A["510202"], debit=Decimal("7500"), cost_center=cc["FAC"]),
            gl.LineSpec(account=A["520101"], debit=Decimal("85000"), cost_center=cc["ADM"]),
            gl.LineSpec(account=A["210201"], credit=Decimal("107500")),
        ], user=users["accountant"], reference="PAYROLL", narration="Monthly payroll accrual")
        gl.post_entry(e, cfo)

        # Sales with customer withholding (1%) and VAT 14%
        so = SalesOrder.objects.create(company=c, partner=cust, date=d0 + timedelta(days=20), currency_id="EGP",
                                       warehouse=wh, payment_term=net30, created_by=users["clerk"])
        SalesOrderLine.objects.create(order=so, product=cabinet, quantity=25, unit_price=6500, tax=T["VAT14-S"])
        SalesOrderLine.objects.create(order=so, product=install, quantity=25, unit_price=500, tax=T["VAT14-S"])
        sales.confirm(so, cfo)
        sales.deliver(so, cfo, on_date=d0 + timedelta(days=22))
        inv = billing.post_invoice(sales.create_invoice(so, cfo, d0 + timedelta(days=22)), cfo)
        rc = Payment.objects.create(company=c, direction="inbound", partner=cust, journal=J["BNK"],
                                    date=d0 + timedelta(days=30), amount=Decimal("100000"), currency_id="EGP",
                                    method="cheque", cheque_number="889201", cheque_bank="CIB",
                                    cheque_due_date=d0 + timedelta(days=40))
        billing.post_payment(rc, cfo, [(Invoice.objects.get(pk=inv.pk), Decimal("100000"))])
        billing.cheque_deposit(rc, cfo)
        billing.cheque_clear(rc, cfo, d0 + timedelta(days=41))
        inv2 = Invoice.objects.create(company=c, move_type="out_invoice", partner=cust2, currency_id="EGP",
                                      date=d0 + timedelta(days=25), payment_term=net30)
        InvoiceLine.objects.create(invoice=inv2, product=cabinet, description="Steel office cabinet",
                                   account=A["410101"], quantity=5, unit_price=6500, tax=T["VAT14-S"])
        billing.post_invoice(inv2, cfo)
        # Maintenance bill with 3% withholding on services
        mb = Invoice.objects.create(company=c, move_type="in_invoice", partner=maint_v, currency_id="EGP",
                                    date=d0 + timedelta(days=26), reference="DMS-551")
        InvoiceLine.objects.create(invoice=mb, description="Machine maintenance", account=A["520203"], quantity=1,
                                   unit_price=20000, tax=T["VAT14-P"], withholding_tax=T["WHT3-SRV-P"],
                                   cost_center=cc["FAC"])
        billing.post_invoice(mb, cfo)

        # Fixed assets: machine bought on a bill, registered and depreciated
        fb = Invoice.objects.create(company=c, move_type="in_invoice", partner=steel_v, currency_id="EGP",
                                    date=d0 + timedelta(days=1), reference="ESS-MCH-01")
        InvoiceLine.objects.create(invoice=fb, description="CNC press brake", account=A["120103"], quantity=1,
                                   unit_price=600000, tax=T["VAT5-P"], cost_center=cc["FAC"])
        fb = billing.post_invoice(fb, cfo)
        asset = fa.create_from_bill_line(fb.lines.first(), cfo)
        asset.location, asset.custodian = "Factory hall A", "Eng. Tarek"
        asset.save()
        fa.confirm(asset, cfo)
        fa.run_depreciation(c, d0 + timedelta(days=45), cfo)
        it = AssetCategory.objects.get(company=c, name="Computers & IT")
        laptops = Asset.objects.create(company=c, code="FA-IT-01", name="Laptops (10)", category=it,
                                       acquisition_date=d0, in_service_date=d0, cost=Decimal("250000"),
                                       useful_life_months=36, method="straight_line", cost_center=cc["ADM"])
        opening_trial_balance(c, d0, [{"account_code": "120106", "debit": "250000"},
                                      {"account_code": "110110", "credit": "250000"}], cfo)
        fa.confirm(laptops, cfo)

        # Budget, recurring rent
        fy = FiscalYear.objects.get(company=c)
        budget = Budget.objects.create(company=c, name=f"Budget {fy.name}", fiscal_year=fy)
        rows = []
        for p in fy.periods.all():
            rows += [{"account": A["410101"].pk, "period": p.pk, "amount": "350000"},
                     {"account": A["520101"].pk, "period": p.pk, "amount": "80000"},
                     {"account": A["520201"].pk, "period": p.pk, "amount": "40000"}]
        set_budget_lines(budget, rows)
        RecurringEntry.objects.create(company=c, name="Office rent", journal=J["GEN"], narration="Monthly office rent",
                                      next_date=d0 + timedelta(days=27), auto_post=True, created_by=cfo,
                                      lines=[{"account": A["520201"].pk, "debit": "40000", "cost_center": cc["ADM"].pk},
                                             {"account": A["110110"].pk, "credit": "40000"}])
        run_due(c, today, cfo)

        # Bank statement for reconciliation practice
        stmt = (f"date,description,reference,amount\n"
                f"{(d0 + timedelta(days=41)).isoformat()},CHQ 889201 CIB collection,889201,100000\n"
                f"{(d0 + timedelta(days=42)).isoformat()},Bank charges,,-150\n")
        bal = gl_balance(c, A["110110"], d0 + timedelta(days=41))
        st = bank.import_csv(c, J["BNK"], stmt, bal, bal + Decimal("99850"), cfo, "CIB statement")
        bank.auto_match(st)
        self.stdout.write(self.style.SUCCESS(
            "Egyptian demo 'Nile Industries SAE' ready (switch company in the sidebar). Same demo users."))


def gl_balance(company, account, before):
    from django.db.models import Sum
    from apps.ledger.models import JournalLine
    agg = JournalLine.objects.filter(company=company, account=account, posted=True, date__lt=before).aggregate(
        d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or 0) - (agg["c"] or 0)
