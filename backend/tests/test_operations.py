import pytest

from apps.approvals import services as approvals
from apps.approvals.models import ApprovalRequest
from apps.billing import services as billing
from apps.billing.models import Invoice, InvoiceLine, Payment
from apps.core.exceptions import BusinessError
from apps.core.models import ExchangeRate
from apps.inventory import services as stock
from apps.inventory.models import MoveType
from apps.ledger import reports
from apps.purchasing import services as purchasing
from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine
from apps.sales import services as sales
from apps.sales.models import SalesOrder, SalesOrderLine

from .conftest import D


def receive(env, prod, qty, cost, day):
    return stock.receive(env.c, prod, env.wh, qty, cost, env.d(5, day), env.cfo, counter_account=env.A["320102"],
                         move_type=MoveType.OPENING)


def issue(env, prod, qty, day=20):
    return stock.issue(env.c, prod, env.wh, qty, env.d(5, day), env.cfo, counter_account=env.A["510101"])


def test_fifo_costing(env):
    receive(env, env.fifo_prod, 10, 5, 1)
    receive(env, env.fifo_prod, 10, 7, 2)
    m = issue(env, env.fifo_prod, 15)
    assert m.value == D(10 * 5 + 5 * 7)
    m = issue(env, env.fifo_prod, 5)
    assert m.value == D(35)


def test_average_costing(env):
    receive(env, env.avg_prod, 10, 5, 1)
    receive(env, env.avg_prod, 10, 7, 2)
    m = issue(env, env.avg_prod, 15)
    assert m.value == D(90)
    m = issue(env, env.avg_prod, 5)  # last units take the exact remainder
    assert m.value == D(30)


def test_average_rounding_remainder(env):
    receive(env, env.avg_prod, 3, D("10"), 1)
    receive(env, env.avg_prod, 3, D("10.01"), 2)
    a = issue(env, env.avg_prod, 1).value
    b = issue(env, env.avg_prod, 2).value
    c = issue(env, env.avg_prod, 3).value
    assert a + b + c == D("60.03")


def test_negative_stock_blocked(env):
    receive(env, env.fifo_prod, 2, 5, 1)
    with pytest.raises(BusinessError, match="Insufficient stock"):
        issue(env, env.fifo_prod, 3)


def test_stock_valuation_equals_gl(env):
    receive(env, env.fifo_prod, 10, 5, 1)
    receive(env, env.avg_prod, 4, D("3.3333"), 1)
    issue(env, env.fifo_prod, 3)
    issue(env, env.avg_prod, 1)
    stock.adjust(env.c, env.avg_prod, env.wh, 2, env.d(5, 21), env.cfo, reason="count")
    v = stock.valuation_report(env.c)
    tb = reports.trial_balance(env.c, env.d(1, 1), env.d(12, 31))
    gl_inv = next(r for r in tb["rows"] if r["code"] == "110301")
    assert D(v["total_value"]) == D(gl_inv["closing"])


def _po(env, qty=10, price=100, user=None):
    po = PurchaseOrder.objects.create(company=env.c, partner=env.vendor, date=env.d(6, 1), currency_id="USD",
                                      warehouse=env.wh, created_by=user or env.clerk)
    PurchaseOrderLine.objects.create(order=po, product=env.fifo_prod, quantity=qty, unit_price=price, tax=env.vat_p)
    return po


def test_procure_to_pay_with_three_way_match_and_variance(env):
    po = _po(env)
    purchasing.confirm(po, env.accountant)
    purchasing.receive(po, env.accountant, {po.lines.first().pk: 6}, env.d(6, 2))
    bill = purchasing.create_bill(po, env.accountant, env.d(6, 3), "V-1")
    ln = bill.lines.first()
    assert ln.quantity == 6
    ln.quantity = 8  # more than received
    ln.save()
    with pytest.raises(BusinessError) as ex:
        billing.post_invoice(bill, env.accountant)
    assert "received" in str(ex.value.details)
    ln.quantity = 6
    ln.unit_price = 101  # 1% above PO, company tolerance 0% → only level 4+ may post
    ln.save()
    with pytest.raises(BusinessError):
        billing.post_invoice(bill, env.accountant)
    bill = billing.post_invoice(bill, env.controller)
    assert bill.state == "posted" and bill.amount_total == D("696.90")
    tb = {r["code"]: D(r["closing"]) for r in reports.trial_balance(env.c, env.d(1, 1), env.d(12, 31))["rows"]}
    assert tb["210102"] == 0          # GRNI fully cleared for the received qty
    assert tb["510102"] == D(6)       # purchase price variance 6 x 1
    assert tb["110301"] == D(600)     # inventory at receipt cost
    assert tb["110403"] == D("90.90")  # input VAT
    assert tb["210101"] == D("-696.90")
    pay = Payment.objects.create(company=env.c, direction="outbound", partner=env.vendor, journal=env.J["BNK"],
                                 date=env.d(6, 10), amount=D("696.90"), currency_id="USD")
    billing.post_payment(pay, env.accountant, [(Invoice.objects.get(pk=bill.pk), D("696.90"))])
    assert Invoice.objects.get(pk=bill.pk).payment_state == "paid"
    assert billing.subledger_reconciliation(env.c, env.d(12, 31))["payable"]["reconciled"]


def test_duplicate_vendor_bill_reference_blocked(env):
    from django.db import IntegrityError, transaction
    Invoice.objects.create(company=env.c, move_type="in_invoice", partner=env.vendor, date=env.d(6, 1),
                           currency_id="USD", reference="INV-77")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Invoice.objects.create(company=env.c, move_type="in_invoice", partner=env.vendor, date=env.d(6, 2),
                                   currency_id="USD", reference="INV-77")


def test_order_to_cash_credit_note_and_aging(env):
    receive(env, env.fifo_prod, 10, 40, 1)
    so = SalesOrder.objects.create(company=env.c, partner=env.customer, date=env.d(6, 1), currency_id="USD",
                                   warehouse=env.wh, created_by=env.clerk)
    SalesOrderLine.objects.create(order=so, product=env.fifo_prod, quantity=4, unit_price=100, tax=env.vat_s)
    sales.confirm(so, env.accountant)
    with pytest.raises(BusinessError, match="Nothing to invoice"):
        sales.create_invoice(so, env.accountant)
    sales.deliver(so, env.accountant, on_date=env.d(6, 2))
    inv = billing.post_invoice(sales.create_invoice(so, env.accountant, env.d(6, 2)), env.accountant)
    assert inv.amount_total == D(460)
    tb = {r["code"]: D(r["closing"]) for r in reports.trial_balance(env.c, env.d(1, 1), env.d(12, 31))["rows"]}
    assert tb["510101"] == D(160) and tb["410101"] == D(-400) and tb["210301"] == D(-60)
    cn = billing.create_refund(inv, env.senior, reason="1 unit damaged")
    ln = cn.lines.first()
    ln.quantity = 1
    ln.save()
    cn = billing.post_invoice(cn, env.senior)
    inv.refresh_from_db()
    assert cn.amount_total == D(115) and inv.amount_residual == D(345) and inv.payment_state == "partial"
    ag = billing.aging(env.c, "receivable", env.d(12, 31))
    assert D(ag["totals"]["total"]) == D(345)
    assert billing.subledger_reconciliation(env.c, env.d(12, 31))["receivable"]["reconciled"]


def test_credit_limit(env):
    env.customer.credit_check = True
    env.customer.credit_limit = D(100)
    env.customer.save()
    so = SalesOrder.objects.create(company=env.c, partner=env.customer, date=env.d(6, 1), currency_id="USD",
                                   warehouse=env.wh)
    SalesOrderLine.objects.create(order=so, product=env.fifo_prod, quantity=2, unit_price=100)
    with pytest.raises(BusinessError, match="Credit limit"):
        sales.confirm(so, env.accountant)


def _eur_bill(env, amount):
    ExchangeRate.objects.create(company=env.c, currency_id="EUR", date=env.d(1, 1), rate=D("1.10"))
    ExchangeRate.objects.create(company=env.c, currency_id="EUR", date=env.d(7, 1), rate=D("1.20"))
    bill = Invoice.objects.create(company=env.c, move_type="in_invoice", partner=env.vendor, currency_id="EUR",
                                  date=env.d(6, 1), reference="EU-1")
    InvoiceLine.objects.create(invoice=bill, description="Consulting", account=env.A["520303"], quantity=1,
                               unit_price=amount)
    return billing.post_invoice(bill, env.accountant)


def test_fx_loss_on_vendor_payment(env):
    bill = _eur_bill(env, 1000)  # booked 1100 USD
    pay = Payment.objects.create(company=env.c, direction="outbound", partner=env.vendor, journal=env.J["BNK2"],
                                 date=env.d(7, 5), amount=D(1000), currency_id="EUR")  # paid 1200 USD
    billing.post_payment(pay, env.accountant, [(Invoice.objects.get(pk=bill.pk), D(1000))])
    tb = {r["code"]: D(r["closing"]) for r in reports.trial_balance(env.c, env.d(1, 1), env.d(12, 31))["rows"]}
    assert tb["530101"] == D(100)  # realised FX loss
    assert tb.get("210101", 0) == 0  # AP fully cleared in base currency
    assert tb["110111"] == D(-1200)


def test_approval_workflow_and_segregation_of_duties(env):
    po = _po(env, qty=100, price=600)  # 69,000 incl. VAT → rule "PO above 50k → Controller"
    po = purchasing.confirm(po, env.accountant)
    assert po.state == "to_approve"
    req = ApprovalRequest.objects.get(document_id=po.pk, document_type="purchase_order")
    with pytest.raises(BusinessError, match="level 4"):
        approvals.decide(req, env.senior, True)
    with pytest.raises(BusinessError, match="Segregation"):
        approvals.decide(req, env.accountant, True)  # submitted it
    approvals.decide(req, env.controller, True)
    po.refresh_from_db()
    assert po.state == "approved"
    po = purchasing.confirm(po, env.accountant)
    assert po.state == "confirmed" and po.number.startswith("PO/")


def test_approval_limit(env):
    from apps.approvals.models import ApprovalRule
    ApprovalRule.objects.create(company=env.c, name="all bills", document_type="vendor_bill", min_amount=0,
                                approver_levels=[2])
    bill = Invoice.objects.create(company=env.c, move_type="in_invoice", partner=env.vendor, currency_id="USD",
                                  date=env.d(6, 1), created_by=env.clerk)
    InvoiceLine.objects.create(invoice=bill, description="x", account=env.A["520303"], quantity=1, unit_price=20000)
    bill = billing.post_invoice(bill, env.clerk)
    req = ApprovalRequest.objects.get(document_id=bill.pk)
    with pytest.raises(BusinessError, match="approval limit"):
        approvals.decide(req, env.accountant, True)  # limit 10,000
    approvals.decide(req, env.senior, True)          # limit 50,000
    assert billing.post_invoice(Invoice.objects.get(pk=bill.pk), env.accountant).state == "posted"


def test_cancel_posted_invoice_reverses_gl(env):
    inv = Invoice.objects.create(company=env.c, move_type="out_invoice", partner=env.customer, currency_id="USD",
                                 date=env.d(6, 1))
    InvoiceLine.objects.create(invoice=inv, description="svc", account=env.A["410102"], quantity=1, unit_price=100,
                               tax=env.vat_s)
    inv = billing.post_invoice(inv, env.accountant)
    billing.cancel_invoice(inv, env.senior, "wrong customer")
    tb = reports.trial_balance(env.c, env.d(1, 1), env.d(12, 31))
    assert all(D(r["closing"]) == 0 for r in tb["rows"])
