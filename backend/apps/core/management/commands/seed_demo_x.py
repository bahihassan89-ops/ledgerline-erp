"""Company X: a ~1bn EGP/year steel-rack manufacturer exercising every module across a full fiscal
year (2025) in one shot -- procurement (3-way match, withholding), manufacturing (BOM/work center/
actual absorption costing), AR & AP (aging, cheques, write-off), GL, fixed assets (capex +
depreciation), an owner's loan, and bank reconciliation. Built to be read back with the reporting
functions (income_statement, balance_sheet, cash_flow, aging, subledger_reconciliation, asset
register) for a one-shot "show me the results" demo.

    python manage.py seed_demo      (first, to create the demo users)
    python manage.py seed_demo_x
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.approvals.models import ApprovalRequest, ApprovalRule, DocumentType
from apps.approvals.services import decide as approvals_decide
from apps.assets import services as fa
from apps.banking import services as bank
from apps.billing import services as billing
from apps.billing.models import Invoice, InvoiceLine, Payment
from apps.core import context
from apps.core.models import Company, CostCenter
from apps.einvoice.models import EtaSettings
from apps.iam.models import Membership, Role, User
from apps.inventory.models import Product, ProductCategory, Warehouse
from apps.ledger import services as gl
from apps.ledger.imports import opening_trial_balance
from apps.ledger.models import Account, Journal, JournalLine, Tax
from apps.manufacturing import services as mrp
from apps.manufacturing.models import Bom, BomComponent, BomOperation, ManufacturingOrder, WorkCenter
from apps.partners.models import Partner, PaymentTerm
from apps.purchasing import services as purchasing
from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine
from apps.sales import services as sales
from apps.sales.models import SalesOrder, SalesOrderLine

from .seed_demo import DEMO_PASSWORD, USERS

LEVEL_NAME = {3: "senior", 4: "controller", 5: "cfo"}
CODE = "demo-x"
YEAR = 2025


class Command(BaseCommand):
    help = "Create 'X Industrial Manufacturing SAE', a ~1bn EGP/year production company, full FY2025"

    def handle(self, *a, **o):
        token = context.set_context(context.RequestContext())
        try:
            self.seed()
        finally:
            context.reset(token)

    @transaction.atomic
    def seed(self):
        if Company.objects.filter(code=CODE).exists():
            self.stdout.write("Company X already exists.")
            return
        call_command("setup_company", name="X Industrial Manufacturing SAE",
                     name_ar="إكس للصناعات المعدنية ش.م.م", code=CODE, country="EG", year=YEAR)
        c = Company.objects.get(code=CODE)
        c.tax_number, c.registration_number, c.tax_office = "900100200", "CR-55123", "مأمورية ضرائب الشركات المساهمة"
        c.address = "6th of October Industrial Zone, Giza"
        c.save()
        EtaSettings.objects.create(company=c, enabled=False, environment="preprod", taxpayer_activity_code="2591",
                                   governate="Giza", region_city="6th of October", street="Industrial Zone 3",
                                   building_number="12", signer_mode="unsigned_preprod")

        users = {}
        for uname, role, first, last in USERS:
            u, _ = User.objects.get_or_create(username=uname, defaults={"email": f"{uname}@demo.local",
                                                                        "first_name": first, "last_name": last})
            if not u.password or not u.has_usable_password():
                u.set_password(DEMO_PASSWORD)
                u.save()
            m, _ = Membership.objects.get_or_create(user=u, company=c)
            m.roles.set([Role.objects.get(company=c, name=role)])
            users[uname] = u
        cfo = users["cfo"]
        ctx = context.get()
        ctx.user, ctx.actor_label, ctx.company_id = cfo, "seed_demo_x", c.pk

        # Raise approval limits/thresholds to fit a company this size -- editable per handbook section 17.
        for name, limit in [("Senior Accountant", "60000000"), ("Financial Controller", "80000000")]:
            r = Role.objects.get(company=c, name=name)
            r.approval_limit = Decimal(limit)
            r.save()
        for name, amt in [("PO above threshold → Controller", "5000000"),
                          ("Large PO → Controller + CFO", "30000000"),
                          ("Large vendor bill → Controller", "30000000"),
                          ("Payment above threshold → Controller", "10000000"),
                          ("Large payment → Controller + CFO", "60000000"),
                          ("Large sales order → Senior Accountant", "10000000")]:
            ApprovalRule.objects.filter(company=c, name=name).update(min_amount=Decimal(amt))

        A = {x.code: x for x in Account.objects.filter(company=c)}
        J = {x.code: x for x in Journal.objects.filter(company=c)}
        T = {x.code: x for x in Tax.objects.filter(company=c)}
        wh = Warehouse.objects.get(company=c, code="MAIN")
        net30 = PaymentTerm.objects.get(company=c, name="Net 30")
        net60 = PaymentTerm.objects.get(company=c, name="Net 60")
        cats = {x.name: x for x in ProductCategory.objects.filter(company=c)}
        for code, name in [("ADM", "الإدارة / Administration"), ("FAC", "المصنع / Factory"),
                           ("SLS", "المبيعات / Sales")]:
            CostCenter.objects.create(company=c, code=code, name=name)
        cc = {x.code: x for x in CostCenter.objects.filter(company=c)}

        # --------------------------------------------------------------------------- approval helpers

        def approve_pending(doc_type, doc):
            req = (ApprovalRequest.objects.filter(company=c, document_type=doc_type, document_id=doc.pk,
                                                  state=ApprovalRequest.State.PENDING).order_by("-id").first())
            if not req:
                return
            for _ in range(len(req.levels)):
                req.refresh_from_db()
                lvl = req.levels[req.current_step]
                approvals_decide(req, users[LEVEL_NAME[lvl]], True)

        def confirm_po(po):
            purchasing.confirm(po, users["clerk"])
            po.refresh_from_db()
            if po.state == "to_approve":
                approve_pending(DocumentType.PURCHASE_ORDER, po)
                po.refresh_from_db()
                purchasing.confirm(po, users["clerk"])
            return po

        def confirm_so(so):
            sales.confirm(so, users["clerk"])
            so.refresh_from_db()
            if so.state == "to_approve":
                approve_pending(DocumentType.SALES_ORDER, so)
                so.refresh_from_db()
                sales.confirm(so, users["clerk"])
            return so

        def post_inv(inv):
            inv = billing.post_invoice(inv, users["clerk"])
            if inv.state == "to_approve":
                doc_type = DocumentType.CUSTOMER_INVOICE if inv.is_customer else DocumentType.VENDOR_BILL
                approve_pending(doc_type, inv)
                inv.refresh_from_db()
                inv = billing.post_invoice(inv, users["clerk"])
            return inv

        def post_pay(pay, allocations):
            pay = billing.post_payment(pay, users["clerk"], allocations)
            if pay.state == "to_approve":
                approve_pending(DocumentType.PAYMENT, pay)
                pay.refresh_from_db()
                pay = billing.post_payment(pay, users["clerk"], allocations)
            return pay

        # --------------------------------------------------------------------------- opening balances

        opening_trial_balance(c, date(YEAR, 1, 1), [
            {"account_code": "110110", "debit": "12000000"},
            {"account_code": "310101", "credit": "10000000"},
            {"account_code": "320101", "credit": "2000000"},
        ], cfo)

        # --------------------------------------------------------------------------- partners

        custA = Partner.objects.create(company=c, code="CX01", name="Delta Construction Holdings",
                                       name_ar="دلتا للمقاولات", is_customer=True, tax_number="700800900",
                                       governate="Cairo", region_city="New Cairo", street="Teseen St",
                                       building_number="120", payment_term=net30)
        custB = Partner.objects.create(company=c, code="CX02", name="Nile Valley Contractors", is_customer=True,
                                       tax_number="710800900", governate="Giza", region_city="Haram",
                                       street="Faisal St", building_number="45", payment_term=net60)
        custC = Partner.objects.create(company=c, code="CX03", name="Red Sea Infrastructure Co.", is_customer=True,
                                       tax_number="720800900", governate="Red Sea", region_city="Hurghada",
                                       street="Corniche Rd", building_number="9", payment_term=net30)
        steel_v = Partner.objects.create(company=c, code="VX01", name="Suez Steel Mills",
                                         name_ar="مصانع السويس للصلب", is_vendor=True, tax_number="730800900",
                                         payment_term=net30, withholding_tax=T["WHT1-SUP-P"])
        fit_v = Partner.objects.create(company=c, code="VX02", name="Alexandria Metal Fittings", is_vendor=True,
                                       tax_number="740800900", payment_term=net30, withholding_tax=T["WHT1-SUP-P"])
        maint_v = Partner.objects.create(company=c, code="VX03", name="October Industrial Maintenance",
                                         is_vendor=True, tax_number="750800900", payment_term=net30,
                                         withholding_tax=T["WHT3-SRV-P"])
        util_v = Partner.objects.create(company=c, code="VX04", name="Giza Electricity Distribution Co.",
                                        is_vendor=True, tax_number="760800900", payment_term=net30)

        # --------------------------------------------------------------------------- products, BOM, work center

        steel = Product.objects.create(company=c, sku="RM-STEEL2", name="Hot-rolled steel coil",
                                       name_ar="لفائف صلب ساخنة", category=cats["Raw Materials"], uom="Ton",
                                       purchase_price=Decimal("30000"), purchase_tax=T["VAT14-P"])
        fittings = Product.objects.create(company=c, sku="RM-FIT2", name="Racking fittings set",
                                          category=cats["Raw Materials"], uom="Set", purchase_price=Decimal("150"),
                                          purchase_tax=T["VAT14-P"])
        rack = Product.objects.create(company=c, sku="FG-RACK", name="Industrial steel racking system",
                                      name_ar="نظام أرفف تخزين معدنية صناعية", category=cats["Merchandise (FIFO)"],
                                      uom="Unit", sale_price=Decimal("28000"), sale_tax=T["VAT14-S"],
                                      eta_item_code="EG-900100200-RACK1")
        # capacity_minutes_per_day represents the whole fabrication hall (many parallel stations/shifts),
        # sized so one month's output (360,000 minutes) schedules within its own day -- finite scheduling
        # itself isn't the focus of this demo.
        wc = WorkCenter.objects.create(company=c, code="FAB-X", name="Racking fabrication line",
                                       labour_rate=Decimal("250"), overhead_rate=Decimal("180"), cost_center=cc["FAC"],
                                       capacity_minutes_per_day=Decimal("400000"))
        bom = Bom.objects.create(company=c, code="BOM-RACK-01", product=rack, quantity=1)
        BomComponent.objects.create(bom=bom, product=steel, quantity=Decimal("0.5"), scrap_pct=Decimal("2"))
        BomComponent.objects.create(bom=bom, product=fittings, quantity=Decimal("3"))
        BomOperation.objects.create(bom=bom, name="Fabricate & assemble", work_center=wc, minutes=Decimal("120"))

        # --------------------------------------------------------------------------- owner's loan (Jan injection)

        e = gl.create_entry(c, J["GEN"], date(YEAR, 1, 15), [
            gl.LineSpec(account=A["110110"], debit=Decimal("20000000")),
            gl.LineSpec(account=A["220101"], credit=Decimal("20000000")),
        ], user=users["accountant"], reference="OWNER-LOAN-IN", narration="Owner's long-term loan to the company")
        gl.post_entry(e, cfo)

        # --------------------------------------------------------------------------- monthly production/trade cycle

        steel_bills, maint_bills, util_bills = [], [], []
        inv_by_cust = {custA: [], custB: [], custC: []}
        MONTHLY_UNITS = {custA: 1500, custB: 900, custC: 600}

        for m in range(1, 13):
            last_day = calendar.monthrange(YEAR, m)[1]

            def D(day):
                return date(YEAR, m, min(day, last_day))

            # Steel purchase (1600 tons buffer against 1530 tons planned consumption)
            po = PurchaseOrder.objects.create(company=c, partner=steel_v, date=D(2), currency_id="EGP",
                                              warehouse=wh, payment_term=net30, created_by=users["clerk"])
            PurchaseOrderLine.objects.create(order=po, product=steel, quantity=1600, unit_price=30000,
                                             tax=T["VAT14-P"])
            confirm_po(po)
            purchasing.receive(po, users["clerk"], on_date=D(5))
            sb = post_inv(purchasing.create_bill(po, users["clerk"], D(6), f"SSM-{YEAR}-{m:02d}"))
            steel_bills.append(sb)

            # Fittings purchase (9200 sets buffer against 9000 planned) -- paid promptly every month
            po2 = PurchaseOrder.objects.create(company=c, partner=fit_v, date=D(3), currency_id="EGP",
                                               warehouse=wh, payment_term=net30, created_by=users["clerk"])
            PurchaseOrderLine.objects.create(order=po2, product=fittings, quantity=9200, unit_price=150,
                                             tax=T["VAT14-P"])
            confirm_po(po2)
            purchasing.receive(po2, users["clerk"], on_date=D(6))
            fb = post_inv(purchasing.create_bill(po2, users["clerk"], D(7), f"AMF-{YEAR}-{m:02d}"))
            pay2 = Payment.objects.create(company=c, direction="outbound", partner=fit_v, journal=J["BNK"],
                                          date=D(20), amount=fb.amount_total, currency_id="EGP", method="transfer")
            post_pay(pay2, [(Invoice.objects.get(pk=fb.pk), fb.amount_total)])

            # Quarterly maintenance bill
            if m in (3, 6, 9, 12):
                mb = Invoice.objects.create(company=c, move_type="in_invoice", partner=maint_v, currency_id="EGP",
                                            date=D(10), reference=f"OIM-{YEAR}-Q{m // 3}")
                InvoiceLine.objects.create(invoice=mb, description="Quarterly machine maintenance",
                                           account=A["520203"], quantity=1, unit_price=500000, tax=T["VAT14-P"],
                                           withholding_tax=T["WHT3-SRV-P"], cost_center=cc["FAC"])
                maint_bills.append((m, post_inv(mb)))

            # Monthly utility bill -- paid promptly
            ub = Invoice.objects.create(company=c, move_type="in_invoice", partner=util_v, currency_id="EGP",
                                        date=D(10), reference=f"GED-{YEAR}-{m:02d}")
            InvoiceLine.objects.create(invoice=ub, description="Factory electricity", account=A["520202"],
                                       quantity=1, unit_price=250000, tax=T["VAT14-P"], cost_center=cc["FAC"])
            ub = post_inv(ub)
            util_bills.append(ub)
            pay3 = Payment.objects.create(company=c, direction="outbound", partner=util_v, journal=J["BNK"],
                                          date=D(22), amount=ub.amount_total, currency_id="EGP", method="transfer")
            post_pay(pay3, [(Invoice.objects.get(pk=ub.pk), ub.amount_total)])

            # Manufacturing: 3,000 racks this month
            mo = ManufacturingOrder.objects.create(company=c, product=rack, bom=bom, quantity=3000, warehouse=wh,
                                                   planned_date=D(8))
            mrp.confirm(mo, cfo)
            mrp.consume(mo, cfo, on_date=D(9))
            op = mo.operations.first()
            mrp.record_operations(mo, cfo, {op.pk: 360000}, on_date=D(15))
            mrp.produce(mo, cfo, 3000, scrap_qty=0, on_date=D(16), finish=True)

            # Monthly payroll: factory wages/overhead + admin salaries, paid directly from the bank
            pe = gl.create_entry(c, J["GEN"], D(20), [
                gl.LineSpec(account=A["510201"], debit=Decimal("1500000"), cost_center=cc["FAC"]),
                gl.LineSpec(account=A["510202"], debit=Decimal("1080000"), cost_center=cc["FAC"]),
                gl.LineSpec(account=A["520101"], debit=Decimal("800000"), cost_center=cc["ADM"]),
                gl.LineSpec(account=A["110110"], credit=Decimal("3380000")),
            ], user=users["accountant"], reference=f"PAYROLL-{m:02d}", narration="Monthly factory & admin payroll")
            gl.post_entry(pe, cfo)

            # Sales: 3 customers, same product, differentiated volumes
            for cust, qty in MONTHLY_UNITS.items():
                so = SalesOrder.objects.create(company=c, partner=cust, date=D(18), currency_id="EGP",
                                               warehouse=wh, payment_term=cust.payment_term,
                                               created_by=users["clerk"])
                SalesOrderLine.objects.create(order=so, product=rack, quantity=qty, unit_price=28000,
                                              tax=T["VAT14-S"])
                confirm_so(so)
                sales.deliver(so, users["clerk"], on_date=D(19))
                inv = post_inv(sales.create_invoice(so, users["clerk"], D(19)))
                inv_by_cust[cust].append((m, inv))

            # Quarterly capex
            capex = {2: ("CNC laser cutting machine", "120103", Decimal("12000000"), T["VAT5-P"]),
                    5: ("Forklift fleet (6 units)", "120104", Decimal("3500000"), T["VAT14-P"]),
                    8: ("ERP & IT infrastructure upgrade", "120106", Decimal("2000000"), T["VAT14-P"]),
                    11: ("Production-line expansion equipment", "120103", Decimal("9500000"), T["VAT5-P"])}
            if m in capex:
                desc, acc_code, amount, tax = capex[m]
                cb = Invoice.objects.create(company=c, move_type="in_invoice", partner=steel_v, currency_id="EGP",
                                            date=D(1), reference=f"CAPEX-{m:02d}")
                InvoiceLine.objects.create(invoice=cb, description=desc, account=A[acc_code], quantity=1,
                                           unit_price=amount, tax=tax, cost_center=cc["FAC"])
                cb = post_inv(cb)
                asset = fa.create_from_bill_line(cb.lines.first(), cfo)
                asset.location, asset.custodian = "Factory hall A", "Eng. Ahmed Fathy"
                asset.save()
                fa.confirm(asset, cfo)
                pay4 = Payment.objects.create(company=c, direction="outbound", partner=steel_v, journal=J["BNK"],
                                              date=D(10), amount=cb.amount_total, currency_id="EGP",
                                              method="transfer")
                post_pay(pay4, [(Invoice.objects.get(pk=cb.pk), cb.amount_total)])

            # Depreciation to month end
            fa.run_depreciation(c, D(last_day), cfo)

        # --------------------------------------------------------------------------- owner's loan partial repayment

        e2 = gl.create_entry(c, J["GEN"], date(YEAR, 12, 15), [
            gl.LineSpec(account=A["220101"], debit=Decimal("5000000")),
            gl.LineSpec(account=A["530102"], debit=Decimal("1200000")),
            gl.LineSpec(account=A["110110"], credit=Decimal("6200000")),
        ], user=users["accountant"], reference="OWNER-LOAN-REPAY", narration="Partial repayment of owner's loan + interest")
        gl.post_entry(e2, cfo)

        # --------------------------------------------------------------------------- settle payables (AP aging)

        for m, sb in enumerate(steel_bills, start=1):
            if m <= 10:
                pay_date = sb.date + timedelta(days=25)
                pay = Payment.objects.create(company=c, direction="outbound", partner=steel_v, journal=J["BNK"],
                                             date=pay_date, amount=sb.amount_total, currency_id="EGP",
                                             method="transfer")
                post_pay(pay, [(Invoice.objects.get(pk=sb.pk), sb.amount_total)])
            # November & December steel bills stay open -> AP aging

        for m, mb in maint_bills:
            if m in (3, 9):
                pay_date = mb.date + timedelta(days=20)
                pay = Payment.objects.create(company=c, direction="outbound", partner=maint_v, journal=J["BNK"],
                                             date=pay_date, amount=mb.amount_total, currency_id="EGP",
                                             method="transfer")
                post_pay(pay, [(Invoice.objects.get(pk=mb.pk), mb.amount_total)])
            # June (vendor dispute, >90 days by year end) and December stay open -> AP aging

        # --------------------------------------------------------------------------- collect receivables (AR aging)

        for m, inv in inv_by_cust[custA]:
            if m <= 11:
                pay_date = inv.date + timedelta(days=25)
                pay = Payment.objects.create(company=c, direction="inbound", partner=custA, journal=J["BNK"],
                                             date=pay_date, amount=inv.amount_total, currency_id="EGP",
                                             method="transfer")
                post_pay(pay, [(Invoice.objects.get(pk=inv.pk), inv.amount_total)])
            # December invoice stays open (current, 0-30 days)

        for m, inv in inv_by_cust[custB]:
            if m <= 9:
                pay_date = inv.date + timedelta(days=55)
                pay = Payment.objects.create(company=c, direction="inbound", partner=custB, journal=J["BNK"],
                                             date=pay_date, amount=inv.amount_total, currency_id="EGP",
                                             method="transfer")
                post_pay(pay, [(Invoice.objects.get(pk=inv.pk), inv.amount_total)])
            # Oct/Nov/Dec invoices stay open -> 31-60 / 61-90 aging

        bad_debt_inv = None
        for m, inv in inv_by_cust[custC]:
            if m == 1:
                bad_debt_inv = inv  # written off below
            elif m <= 5:
                pay_date = inv.date + timedelta(days=100)
                pay = Payment.objects.create(company=c, direction="inbound", partner=custC, journal=J["BNK"],
                                             date=pay_date, amount=inv.amount_total, currency_id="EGP",
                                             method="transfer")
                post_pay(pay, [(Invoice.objects.get(pk=inv.pk), inv.amount_total)])
            # June-December invoices stay open -> deep 61-90 / 90+ aging

        # --------------------------------------------------------------------------- bad debt write-off

        refund = billing.create_refund(bad_debt_inv, users["senior"],
                                       reason="Customer insolvency -- deemed uncollectible, written off per "
                                              "controller approval", return_to_stock=False)
        refund.date = date(YEAR, 12, 20)
        refund.save(update_fields=["date"])
        post_inv(refund)

        # --------------------------------------------------------------------------- bank reconciliation (Q4)

        def gl_balance(company, account, before):
            from django.db.models import Sum
            agg = JournalLine.objects.filter(company=company, account=account, posted=True,
                                             date__lt=before).aggregate(d=Sum("debit"), c=Sum("credit"))
            return (agg["d"] or 0) - (agg["c"] or 0)

        q4_start = date(YEAR, 10, 1)
        lines = ["date,description,reference,amount"]
        recent_pays = Payment.objects.filter(company=c, journal=J["BNK"], date__gte=q4_start,
                                             date__lte=date(YEAR, 12, 31), state="posted").order_by("date")[:30]
        for p in recent_pays:
            sign = 1 if p.direction == "inbound" else -1
            lines.append(f"{p.date.isoformat()},{p.direction} {p.partner.name},{p.pk},{sign * p.amount}")
        lines.append(f"{date(YEAR, 12, 29).isoformat()},Bank charges Q4,,-4500")
        stmt = "\n".join(lines) + "\n"
        opening_bal = gl_balance(c, A["110110"], q4_start)
        movement = sum((1 if p.direction == "inbound" else -1) * p.amount for p in recent_pays) - Decimal("4500")
        st = bank.import_csv(c, J["BNK"], stmt, opening_bal, opening_bal + movement, cfo, "Main bank Q4 statement")
        bank.auto_match(st)

        self.stdout.write(self.style.SUCCESS(
            "Company X 'X Industrial Manufacturing SAE' ready (code=demo-x, FY2025). Same demo users."))
