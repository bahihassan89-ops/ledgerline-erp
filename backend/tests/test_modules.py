"""Cheques, bank reconciliation, fixed assets, FX revaluation, returns, landed costs, budgets,
recurring entries, imports, manufacturing, cash flow and the close checklist."""
from datetime import timedelta

import pytest

from apps.assets import services as fa
from apps.assets.models import Asset, AssetCategory
from apps.banking import services as bank
from apps.billing import services as billing
from apps.billing.models import Invoice, InvoiceLine, Payment
from apps.billing.revaluation import revalue
from apps.core.exceptions import BusinessError
from apps.core.models import ExchangeRate
from apps.inventory import services as stock
from apps.inventory.models import LandedCost, MoveType, Product, ProductValuation
from apps.ledger import reports
from apps.ledger import services as gl
from apps.ledger.models import FiscalYear, JournalEntry
from apps.manufacturing import services as mrp
from apps.manufacturing.models import Bom, BomComponent, BomOperation, ManufacturingOrder, WorkCenter
from apps.planning.models import Budget, RecurringEntry
from apps.planning.services import budget_vs_actual, run_due, set_budget_lines
from apps.purchasing import services as purchasing
from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine
from apps.sales import services as sales
from apps.sales.models import SalesOrder, SalesOrderLine

from .conftest import D, tb


def cust_invoice(env, amount, currency="EGP", day=(3, 1), rate=None):
    inv = Invoice.objects.create(company=env.c, move_type="out_invoice", partner=env.customer, currency_id=currency,
                                 date=env.d(*day), rate=rate or 1)
    InvoiceLine.objects.create(invoice=inv, product=env.service, description="Service", account=env.A["410102"],
                               quantity=1, unit_price=amount, tax=env.T["VAT0-EXP"])
    return billing.post_invoice(inv, env.accountant)


# ------------------------------------------------------------------ cheques

def test_cheque_received_cleared(eg):
    inv = cust_invoice(eg, 10000)
    p = Payment.objects.create(company=eg.c, direction="inbound", partner=eg.customer, journal=eg.J["BNK"],
                               date=eg.d(3, 5), amount=D(10000), currency_id="EGP", method="cheque",
                               cheque_number="123456", cheque_bank="CIB", cheque_due_date=eg.d(3, 20))
    billing.post_payment(p, eg.accountant, [(Invoice.objects.get(pk=inv.pk), D(10000))])
    t = tb(eg)
    assert t["110205"] == D(10000) and t.get("110201", 0) == 0 and t.get("110110", 0) == 0
    billing.cheque_deposit(p, eg.accountant)
    billing.cheque_clear(p, eg.accountant, eg.d(3, 21))
    t = tb(eg)
    assert t.get("110205", 0) == 0 and t["110110"] == D(10000)


def test_cheque_bounced_reopens_invoice(eg):
    inv = cust_invoice(eg, 8000)
    p = Payment.objects.create(company=eg.c, direction="inbound", partner=eg.customer, journal=eg.J["BNK"],
                               date=eg.d(3, 5), amount=D(8000), currency_id="EGP", method="cheque",
                               cheque_number="777", cheque_due_date=eg.d(3, 20))
    billing.post_payment(p, eg.accountant, [(Invoice.objects.get(pk=inv.pk), D(8000))])
    billing.cheque_bounce(p, eg.accountant, "insufficient funds")
    inv.refresh_from_db()
    p.refresh_from_db()
    assert inv.amount_residual == D(8000) and p.cheque_state == "bounced" and p.state == "cancelled"
    t = tb(eg)
    assert t["110201"] == D(8000) and t.get("110205", 0) == 0
    assert billing.subledger_reconciliation(eg.c, eg.d(12, 31))["receivable"]["reconciled"]


# ------------------------------------------------------------------ bank reconciliation

def test_bank_reconciliation(eg):
    e = gl.create_entry(eg.c, eg.J["GEN"], eg.d(2, 28), [
        gl.LineSpec(account=eg.A["110110"], debit=D(50000)), gl.LineSpec(account=eg.A["310101"], credit=D(50000))],
        user=eg.cfo)
    gl.post_entry(e, eg.cfo)
    inv = cust_invoice(eg, 10000)
    p = Payment.objects.create(company=eg.c, direction="inbound", partner=eg.customer, journal=eg.J["BNK"],
                               date=eg.d(3, 3), amount=D(10000), currency_id="EGP", reference="TRF-9")
    billing.post_payment(p, eg.accountant, [(Invoice.objects.get(pk=inv.pk), D(10000))])
    out = Payment.objects.create(company=eg.c, direction="outbound", partner=eg.vendor, journal=eg.J["BNK"],
                                 date=eg.d(3, 30), amount=D(2000), currency_id="EGP")
    billing.post_payment(out, eg.accountant)  # not yet on the statement → outstanding payment
    csv = "Date,Description,Reference,Debit,Credit\n03/03/{y},Transfer from Cairo Retail TRF-9,TRF-9,,10000\n" \
          "31/03/{y},Bank charges,,25,\n".format(y=eg.d(1, 1).year)
    st = bank.import_csv(eg.c, eg.J["BNK"], csv, D(50000), D(59975), eg.accountant)
    assert bank.auto_match(st) == 1
    fee = st.lines.get(matched_line__isnull=True)
    bank.create_entry_for_line(fee, eg.A["520306"], eg.accountant)
    bank.close_statement(st)
    r = bank.reconciliation_report(eg.c, eg.J["BNK"], eg.d(3, 31))
    assert r["reconciled"] and D(r["outstanding_payments_total"]) == D(2000)
    assert D(r["balance_per_bank"]) == D(59975) and D(r["balance_per_books"]) == D(57975)


def test_statement_must_add_up(eg):
    with pytest.raises(BusinessError, match="does not add up"):
        bank.import_csv(eg.c, eg.J["BNK"], "date,amount\n2026-03-01,100\n", D(0), D(90), eg.accountant)


# ------------------------------------------------------------------ fixed assets

def test_asset_depreciation_disposal_and_register(eg):
    cat = AssetCategory.objects.get(company=eg.c, name="Computers & IT")
    a = Asset.objects.create(company=eg.c, code="FA1", name="Server", category=cat, acquisition_date=eg.d(1, 10),
                             in_service_date=eg.d(1, 10), cost=D(36000), useful_life_months=36,
                             method="straight_line")
    e = gl.create_entry(eg.c, eg.J["GEN"], eg.d(1, 10), [
        gl.LineSpec(account=eg.A["120106"], debit=D(36000)), gl.LineSpec(account=eg.A["110110"], credit=D(36000))],
        user=eg.cfo)
    gl.post_entry(e, eg.cfo)
    fa.confirm(a, eg.cfo)
    assert a.lines.count() == 36 and sum(l.amount for l in a.lines.all()) == D(36000)
    fa.run_depreciation(eg.c, eg.d(6, 30), eg.cfo)
    assert tb(eg)["520501"] == D(6000)  # Jan–Jun at 1,000/month
    reg = fa.register(eg.c, eg.d(6, 30))
    assert reg["reconciled"] and D(reg["total_nbv"]) == D(30000)
    fa.dispose(a, eg.d(7, 31), D(32000), eg.A["110110"], eg.cfo)
    t = tb(eg)
    assert t["520501"] == D(7000) and t["420103"] == D(-3000)  # NBV 29,000 sold for 32,000
    assert t.get("120106", 0) == 0 and t.get("120206", 0) == 0


def test_declining_balance_reaches_salvage(eg):
    cat = AssetCategory.objects.get(company=eg.c, name="Motor Vehicles")
    a = Asset.objects.create(company=eg.c, code="FA2", name="Truck", category=cat, acquisition_date=eg.d(1, 1),
                             in_service_date=eg.d(1, 1), cost=D(100000), salvage_value=D(10000),
                             useful_life_months=24, method="declining_balance", declining_rate=D(40))
    fa.confirm(a, eg.cfo)
    total = sum(l.amount for l in a.lines.all())
    assert total == D(90000)
    first = a.lines.first().amount
    assert first == D("3333.33")  # 100,000 × 40% / 12


def test_tax_depreciation_pool(eg):
    cat = AssetCategory.objects.get(company=eg.c, name="Computers & IT")
    a = Asset.objects.create(company=eg.c, code="FA3", name="Laptops", category=cat, acquisition_date=eg.d(1, 1),
                             in_service_date=eg.d(1, 1), cost=D(20000), useful_life_months=36,
                             method="straight_line")
    fa.confirm(a, eg.cfo)
    rep = fa.tax_depreciation(eg.c, eg.d(1, 1).year)
    row = next(r for r in rep["rows"] if r["category"] == "Computers & IT")
    assert D(row["tax_depreciation"]) == D(10000) and D(row["closing_tax_basis"]) == D(10000)  # 50% of basis


def test_capex_from_bill(eg):
    b = Invoice.objects.create(company=eg.c, move_type="in_invoice", partner=eg.vendor, currency_id="EGP",
                               date=eg.d(2, 1), reference="FA-BILL")
    InvoiceLine.objects.create(invoice=b, description="Forklift", account=eg.A["120103"], quantity=1,
                               unit_price=150000, tax=eg.T["VAT14-P"])
    b = billing.post_invoice(b, eg.cfo)
    assert fa.unregistered_capex(eg.c).count() == 1
    a = fa.create_from_bill_line(b.lines.first(), eg.cfo)
    assert a.cost == D(150000) and a.category.name == "Machinery & Equipment"
    assert fa.unregistered_capex(eg.c).count() == 0


# ------------------------------------------------------------------ FX revaluation

def test_fx_revaluation_auto_reverses(eg):
    ExchangeRate.objects.create(company=eg.c, currency_id="USD", date=eg.d(1, 1), rate=D(48))
    ExchangeRate.objects.create(company=eg.c, currency_id="USD", date=eg.d(3, 31), rate=D(50))
    inv = cust_invoice(eg, 1000, currency="USD", day=(3, 10))  # booked 48,000 EGP
    entries = revalue(eg.c, eg.d(3, 31), eg.cfo)
    assert len(entries) == 1
    on_31 = {r["code"]: D(r["closing"]) for r in reports.trial_balance(eg.c, eg.d(1, 1), eg.d(3, 31))["rows"]}
    assert on_31["110201"] == D(50000) and on_31["420104"] == D(-2000)
    after = tb(eg)
    assert after["110201"] == D(48000) and after.get("420104", 0) == 0  # reversed on 1 April
    assert billing.subledger_reconciliation(eg.c, eg.d(3, 31))["receivable"]["reconciled"]
    with pytest.raises(BusinessError, match="already revalued"):
        revalue(eg.c, eg.d(3, 31), eg.cfo)


# ------------------------------------------------------------------ returns & landed cost

def _receive(eg, prod, qty, cost, day):
    return stock.receive(eg.c, prod, eg.wh, qty, cost, eg.d(*day), eg.cfo, counter_account=eg.A["320102"],
                         move_type=MoveType.OPENING)


def test_customer_return_to_stock(eg):
    _receive(eg, eg.item, 10, 400, (2, 1))
    so = SalesOrder.objects.create(company=eg.c, partner=eg.customer, date=eg.d(2, 5), currency_id="EGP",
                                   warehouse=eg.wh)
    SalesOrderLine.objects.create(order=so, product=eg.item, quantity=5, unit_price=1000, tax=eg.T["VAT14-S"])
    sales.confirm(so, eg.accountant)
    sales.deliver(so, eg.accountant, on_date=eg.d(2, 6))
    inv = billing.post_invoice(sales.create_invoice(so, eg.accountant, eg.d(2, 6)), eg.accountant)
    cn = billing.create_refund(inv, eg.cfo, reason="2 units returned", return_to_stock=True)
    ln = cn.lines.first()
    ln.quantity = 2
    ln.save()
    billing.post_invoice(cn, eg.cfo)
    assert ProductValuation.objects.get(product=eg.item).quantity == D(7)
    t = tb(eg)
    assert t["510101"] == D(1200) and t["110301"] == D(2800)  # COGS 3 × 400 remains


def test_vendor_return_to_stock(eg):
    po = PurchaseOrder.objects.create(company=eg.c, partner=eg.vendor, date=eg.d(2, 1), currency_id="EGP",
                                      warehouse=eg.wh)
    PurchaseOrderLine.objects.create(order=po, product=eg.item, quantity=10, unit_price=500, tax=eg.T["VAT14-P"])
    purchasing.confirm(po, eg.cfo)
    purchasing.receive(po, eg.cfo, on_date=eg.d(2, 2))
    b = purchasing.create_bill(po, eg.cfo, eg.d(2, 3), "V-77")
    for ln in b.lines.all():
        ln.withholding_tax = None
        ln.save()
    b = billing.post_invoice(b, eg.cfo)
    dn = billing.create_refund(b, eg.cfo, reason="3 defective", return_to_stock=True)
    ln = dn.lines.first()
    ln.quantity = 3
    ln.save()
    billing.post_invoice(dn, eg.cfo)
    t = tb(eg)
    assert ProductValuation.objects.get(product=eg.item).quantity == D(7)
    assert t["110301"] == D(3500) and t.get("210102", 0) == 0
    assert t["210101"] == D(-(5700 - 1710))


def test_landed_cost_split_between_stock_and_cogs(eg):
    m = _receive(eg, eg.item, 10, 100, (2, 1))
    stock.issue(eg.c, eg.item, eg.wh, 4, eg.d(2, 3), eg.cfo, counter_account=eg.A["510101"])
    lc = LandedCost.objects.create(company=eg.c, date=eg.d(2, 5), description="Customs duty", amount=D(500))
    lc.moves.set([m])
    stock.post_landed_cost(lc, eg.cfo)
    t = tb(eg)
    assert t["110301"] == D(600 + 300) and t["510101"] == D(400 + 200) and t["210104"] == D(-500)
    v = stock.valuation_report(eg.c)
    assert D(v["total_value"]) == t["110301"]
    nxt = stock.issue(eg.c, eg.item, eg.wh, 6, eg.d(2, 6), eg.cfo, counter_account=eg.A["510101"])
    assert nxt.value == D(900)  # 6 units now carry 150 each


# ------------------------------------------------------------------ planning

def test_budget_vs_actual(eg):
    fy = FiscalYear.objects.get(company=eg.c)
    b = Budget.objects.create(company=eg.c, name="Budget 1", fiscal_year=fy)
    periods = list(fy.periods.order_by("number"))
    set_budget_lines(b, [{"account": eg.A["520201"].pk, "period": p.pk, "amount": "10000"} for p in periods])
    e = gl.create_entry(eg.c, eg.J["GEN"], eg.d(1, 31), [
        gl.LineSpec(account=eg.A["520201"], debit=D(12000)), gl.LineSpec(account=eg.A["110110"], credit=D(12000))],
        user=eg.cfo)
    gl.post_entry(e, eg.cfo)
    r = budget_vs_actual(b, eg.d(1, 1), eg.d(1, 31))
    row = next(x for x in r["rows"] if x["code"] == "520201")
    assert D(row["budget"]) == D(10000) and D(row["actual"]) == D(12000) and row["favourable"] is False


def test_recurring_with_auto_reversal(eg):
    rec = RecurringEntry.objects.create(
        company=eg.c, name="Rent accrual", journal=eg.J["GEN"], frequency="monthly", next_date=eg.d(1, 31),
        end_date=eg.d(3, 31), auto_post=True, auto_reverse_days=1, created_by=eg.cfo,
        lines=[{"account": eg.A["520201"].pk, "debit": "5000"}, {"account": eg.A["210202"].pk, "credit": "5000"}])
    created = run_due(eg.c, eg.d(3, 31))
    assert len(created) == 3
    assert all(JournalEntry.objects.filter(reversal_of=e).exists() for e in created)
    rec.refresh_from_db()
    assert rec.active is False


# ------------------------------------------------------------------ go-live imports

def test_go_live_imports_clear_opening_equity(eg):
    from apps.ledger.imports import open_items, opening_trial_balance
    with pytest.raises(BusinessError) as ex:
        opening_trial_balance(eg.c, eg.d(1, 1), [{"account_code": "110201", "debit": "100"}], eg.cfo)
    assert "open customer items" in str(ex.value.details)
    # bank 100,000 + receivables 30,000 + stock 5,000 = capital 120,000 + payables 15,000
    opening_trial_balance(eg.c, eg.d(1, 1), [
        {"account_code": "110110", "debit": "100000"}, {"account_code": "310101", "credit": "120000"}], eg.cfo)
    open_items(eg.c, "receivable", [{"partner_code": "C1", "number": "OLD-9", "date": str(eg.d(1, 1)),
                                     "due_date": str(eg.d(1, 31)), "amount": "30000"}], eg.cfo)
    open_items(eg.c, "payable", [{"partner_code": "V1", "number": "VB-3", "date": str(eg.d(1, 1)),
                                  "amount": "15000"}], eg.cfo)
    stock.receive(eg.c, eg.item, eg.wh, 10, 500, eg.d(1, 1), eg.cfo, counter_account=eg.A["320102"],
                  move_type=MoveType.OPENING)
    t = tb(eg)
    assert t.get("320102", 0) == 0
    ag = billing.aging(eg.c, "receivable", eg.d(3, 1))
    assert D(ag["totals"]["1_30"]) == D(30000)  # due 31 Jan, 29 days overdue


# ------------------------------------------------------------------ manufacturing

def test_manufacturing_actual_costing(eg):
    steel = Product.objects.create(company=eg.c, sku="RM-STEEL", name="Steel sheet", category=eg.raw)
    paint = Product.objects.create(company=eg.c, sku="RM-PAINT", name="Paint (L)", category=eg.raw)
    cabinet = Product.objects.create(company=eg.c, sku="FG-CAB", name="Cabinet", category=eg.fifo)
    stock.receive(eg.c, steel, eg.wh, 100, 200, eg.d(3, 1), eg.cfo, counter_account=eg.A["320102"],
                  move_type=MoveType.OPENING)
    stock.receive(eg.c, paint, eg.wh, 50, 80, eg.d(3, 1), eg.cfo, counter_account=eg.A["320102"],
                  move_type=MoveType.OPENING)
    wc = WorkCenter.objects.create(company=eg.c, code="ASM", name="Assembly", labour_rate=D(120),
                                   overhead_rate=D(60))
    bom = Bom.objects.create(company=eg.c, code="BOM-CAB", product=cabinet, quantity=1)
    BomComponent.objects.create(bom=bom, product=steel, quantity=D(2))
    BomComponent.objects.create(bom=bom, product=paint, quantity=D("0.5"))
    BomOperation.objects.create(bom=bom, name="Assemble & paint", work_center=wc, minutes=D(30))
    mo = ManufacturingOrder.objects.create(company=eg.c, product=cabinet, bom=bom, quantity=D(10),
                                           warehouse=eg.wh, planned_date=eg.d(3, 5))
    mrp.confirm(mo, eg.cfo)
    assert mrp.planned_cost(bom, 10)["total"] == D(4000 + 400 + 900)
    mrp.consume(mo, eg.cfo, on_date=eg.d(3, 5))
    op = mo.operations.first()
    mrp.record_operations(mo, eg.cfo, {op.pk: 330}, on_date=eg.d(3, 6))  # 5.5 hours actually worked
    mrp.produce(mo, eg.cfo, 9, scrap_qty=1, on_date=eg.d(3, 7), finish=True)
    mo.refresh_from_db()
    # material 4,000 + 400; conversion 5.5h × (120 + 60) = 990 → 5,390 total, 1/10 scrapped
    assert mo.material_cost == D(4400) and mo.labour_cost + mo.overhead_cost == D(990)
    assert mo.output_value == D(4851) and mo.scrap_value == D(539) and mo.wip_balance == 0
    t = tb(eg)
    assert t.get("110304", 0) == 0 and t["510203"] == D(-660) and t["510204"] == D(-330) and t["510205"] == D(539)
    v = stock.valuation_report(eg.c)
    inv_gl = t.get("110301", 0) + t.get("110302", 0)
    assert D(v["total_value"]) == inv_gl
    assert mrp.wip_report(eg.c)["reconciled"]


# ------------------------------------------------------------------ statements & checklist

def test_cash_flow_reconciles(eg):
    e = gl.create_entry(eg.c, eg.J["GEN"], eg.d(1, 2), [
        gl.LineSpec(account=eg.A["110110"], debit=D(100000)), gl.LineSpec(account=eg.A["310101"], credit=D(100000))],
        user=eg.cfo)
    gl.post_entry(e, eg.cfo)
    inv = cust_invoice(eg, 20000)
    p = Payment.objects.create(company=eg.c, direction="inbound", partner=eg.customer, journal=eg.J["BNK"],
                               date=eg.d(3, 3), amount=D(15000), currency_id="EGP")
    billing.post_payment(p, eg.accountant, [(Invoice.objects.get(pk=inv.pk), D(15000))])
    e = gl.create_entry(eg.c, eg.J["GEN"], eg.d(3, 4), [
        gl.LineSpec(account=eg.A["120105"], debit=D(30000)), gl.LineSpec(account=eg.A["110110"], credit=D(30000))],
        user=eg.cfo)
    gl.post_entry(e, eg.cfo)
    cf = reports.cash_flow(eg.c, eg.d(1, 1), eg.d(12, 31))
    assert cf["reconciled"] and D(cf["net_change"]) == D(85000)
    assert D(cf["net_profit"]) == D(20000) and D(cf["investing"]["total"]) == D(-30000)
    assert D(cf["financing"]["total"]) == D(100000)


def test_close_checklist(eg):
    from apps.ledger.checklist import run
    from apps.ledger.models import FiscalPeriod
    gl.create_entry(eg.c, eg.J["GEN"], eg.d(1, 15), [
        gl.LineSpec(account=eg.A["520201"], debit=D(1)), gl.LineSpec(account=eg.A["110110"], credit=D(1))],
        user=eg.cfo)
    res = run(eg.c, FiscalPeriod.objects.get(company=eg.c, number=1))
    items = {i["key"]: i for i in res["items"]}
    assert items["drafts"]["status"] == "fail" and not res["ready_to_close"]
    assert items["tb"]["status"] == "ok" and items["subledger"]["status"] == "ok"
