from datetime import date

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


def test_unapplied_credit_note_shown_as_unallocated_not_aged(env):
    # Item #12: an unapplied customer credit note used to be bucketed by its own age -- e.g. a
    # credit note from a year ago landed as a *negative* amount inside "over_90", which reads as
    # "this customer is badly overdue" when the opposite is true (they're owed money). Unapplied
    # payments already get a dedicated "unallocated" bucket; credit notes should get the same
    # treatment instead of distorting an aging bucket.
    cn = Invoice.objects.create(company=env.c, move_type="out_refund", partner=env.customer, date=env.d(1, 1),
                                currency_id="USD", reference="CN-STANDALONE")
    InvoiceLine.objects.create(invoice=cn, description="Goodwill credit", account=env.A["410101"], quantity=1,
                               unit_price=100)
    billing.post_invoice(cn, env.accountant)

    ag = billing.aging(env.c, "receivable", env.d(12, 31))  # ~365 days old -- would have hit over_90 before
    row = ag["rows"][0]
    # aging()'s s() helper stringifies every Decimal for JSON-safety (same convention as
    # elsewhere in the codebase) -- compare through D(...) like the other assertions below,
    # instead of against a bare Decimal which a str value will never equal.
    assert D(row["over_90"]) == D(0) and all(D(row[b]) == D(0) for b, *_ in billing.BUCKETS)
    assert D(row["unallocated"]) == D(100)
    assert D(row["total"]) == D(-100)
    assert D(ag["totals"]["unallocated"]) == D(100)


def test_ecl_default_rate_matrix_applied_to_aging_buckets(env):
    # Item #10: AccountingSettings.ecl_rate_* used to default to 0% across every bucket (an
    # all-zero provision until someone configured it by hand). They now default to a standard
    # conservative simplified-approach matrix; this locks in those defaults and checks
    # ecl_aged_receivables() actually applies them per bucket.
    st = billing.gl.settings_for(env.c)
    assert (st.ecl_rate_current, st.ecl_rate_1_30, st.ecl_rate_31_60, st.ecl_rate_61_90, st.ecl_rate_over_90) == (
        D(0), D(5), D(10), D(25), D(50))

    receive(env, env.fifo_prod, 10, 40, 1)
    so = SalesOrder.objects.create(company=env.c, partner=env.customer, date=env.d(6, 1), currency_id="USD",
                                   warehouse=env.wh, created_by=env.clerk)
    SalesOrderLine.objects.create(order=so, product=env.fifo_prod, quantity=4, unit_price=100, tax=env.vat_s)
    sales.confirm(so, env.accountant)
    sales.deliver(so, env.accountant, on_date=env.d(6, 2))
    billing.post_invoice(sales.create_invoice(so, env.accountant, env.d(6, 2)), env.accountant)

    # as_of 61 days after the invoice date (6/2) falls in the 31-60 bucket's boundary -- pick a
    # date that puts the whole balance (460) squarely in 31_60.
    ecl = billing.ecl_aged_receivables(env.c, env.d(7, 20))
    row = ecl["rows"][0]
    assert D(row["buckets"]["31_60"]["balance"]) == D(460)
    assert D(row["buckets"]["31_60"]["provision"]) == D(46)  # 460 * 10%
    assert D(row["total_provision"]) == D(46)


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


def test_allocate_endpoint_dates_fx_at_payment_date_not_today(env, api):
    """Regression: PaymentViewSet.allocate() -- used to match an already-posted, unallocated payment
    (e.g. an advance) to an invoice sometime later -- used to default the realised-FX entry's date to
    `timezone.localdate()` (today, whenever the accountant got around to clicking "Apply") instead of
    the payment's own date, unlike post_payment()'s immediate-allocation path which has always used
    `on_date=pay.date`. Post the payment unallocated first, then allocate it through the API days
    "later" than its own date, and check the FX entry still lands on the payment's date."""
    from apps.billing.models import Allocation

    bill = _eur_bill(env, 1000)  # booked 1100 USD
    pay = Payment.objects.create(company=env.c, direction="outbound", partner=env.vendor, journal=env.J["BNK2"],
                                 date=env.d(7, 5), amount=D(1000), currency_id="EUR")  # paid 1200 USD
    billing.post_payment(pay, env.accountant, [])  # posted on account, nothing allocated yet
    r = api(env.accountant).post(f"/api/payments/{pay.pk}/allocate/",
                                 {"invoice": bill.pk, "amount": "1000"}, format="json")
    assert r.status_code == 200, r.content
    alloc = Allocation.objects.get(payment=pay, invoice=bill)
    assert alloc.date == env.d(7, 5) and alloc.fx_entry.date == env.d(7, 5)
    assert alloc.fx_entry.date != date.today()  # sanity: this environment's "today" is a different date


def test_draft_fx_payment_gets_real_rate_not_1(env, api):
    # Item #17: Payment.rate defaults to 1 at the model level, and only post_payment() ever
    # resolved a real rate -- so a draft foreign-currency payment sat showing a misleading 1.0
    # the whole time it was a draft (nothing wrong with USD payments, where 1 is actually
    # correct, since base currency always returns rate 1 from resolve_rate()).
    ExchangeRate.objects.create(company=env.c, currency_id="EUR", date=env.d(7, 1), rate=D("1.20"))
    r = api(env.accountant).post("/api/payments/", {
        "direction": "outbound", "partner": env.vendor.pk, "journal": env.J["BNK2"].pk, "date": str(env.d(7, 5)),
        "amount": "1000", "currency": "EUR",
    }, format="json")
    assert r.status_code == 201, r.content
    assert D(r.data["rate"]) == D("1.20")
    pay = Payment.objects.get(pk=r.data["id"])
    assert pay.state == "draft" and pay.rate == D("1.20")

    # A same-currency (base, USD) draft payment should keep showing rate 1 -- that's correct, not stale.
    r2 = api(env.accountant).post("/api/payments/", {
        "direction": "outbound", "partner": env.vendor.pk, "journal": env.J["BNK"].pk, "date": str(env.d(7, 5)),
        "amount": "500", "currency": "USD",
    }, format="json")
    assert r2.status_code == 201, r2.content
    assert D(r2.data["rate"]) == D("1")

    # An explicit, deliberately-chosen rate on a foreign-currency draft is left untouched.
    r3 = api(env.accountant).post("/api/payments/", {
        "direction": "outbound", "partner": env.vendor.pk, "journal": env.J["BNK2"].pk, "date": str(env.d(7, 5)),
        "amount": "200", "currency": "EUR", "rate": "1.35",
    }, format="json")
    assert r3.status_code == 201, r3.content
    assert D(r3.data["rate"]) == D("1.35")


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
