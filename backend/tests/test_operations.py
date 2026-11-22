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
from apps.partners import services as partners
from apps.partners.models import PartnerBankChangeRequest
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


def test_inventory_count_below_threshold_applies_without_root_cause(env):
    # Item #33: the root-cause requirement only bites once a count crosses the same ApprovalRule
    # threshold that would require approval -- small routine counts keep working exactly as before.
    receive(env, env.avg_prod, 4, D("3.3333"), 1)
    count = stock.adjust(env.c, env.avg_prod, env.wh, 6, env.d(5, 21), env.cfo, reason="count")
    assert count.state == "done" and count.move is not None and count.root_cause_code == ""


def test_inventory_count_above_threshold_requires_root_cause(env):
    # Default "Large inventory count variance -> Controller" rule fires at 25,000 (see setup_company.py)
    # -- well above that, a structured root cause is mandatory, mirroring ReturnReason from item #32.
    with pytest.raises(BusinessError, match="root cause"):
        stock.adjust(env.c, env.fifo_prod, env.wh, 100, env.d(5, 21), env.cfo, unit_cost=300, reason="count")
    with pytest.raises(BusinessError, match="Explain"):
        stock.adjust(env.c, env.fifo_prod, env.wh, 100, env.d(5, 21), env.cfo, unit_cost=300,
                     root_cause_code="other")
    count = stock.adjust(env.c, env.fifo_prod, env.wh, 100, env.d(5, 21), env.cfo, unit_cost=300,
                         root_cause_code="counting_error", reason="annual count variance")
    assert count.state == "to_approve"
    req = ApprovalRequest.objects.get(document_id=count.pk, document_type="inventory_count")
    approvals.decide(req, env.controller, True)
    count.refresh_from_db()
    assert count.state == "approved"
    count = stock.apply_count(count, env.controller)
    assert count.state == "done" and count.move is not None


def test_inventory_count_approval_not_netted_across_products(env):
    # A surplus on one product and a shortage on another must each be judged solely on their own
    # abs(diff)*cost, never summed/netted together to dodge the threshold. There is also no bulk
    # multi-row count submission in the API, so this locks in that every call is independently gated.
    receive(env, env.fifo_prod, 100, D("300"), 1)  # on-hand value 30,000
    surplus = stock.adjust(env.c, env.avg_prod, env.wh, 100, env.d(5, 21), env.cfo, unit_cost=300,
                           root_cause_code="receiving_error", reason="surplus found on recount")
    shortage = stock.adjust(env.c, env.fifo_prod, env.wh, 0, env.d(5, 22), env.cfo,
                            root_cause_code="theft_shrinkage", reason="shrinkage on recount")
    assert surplus.state == "to_approve" and surplus.value == D(30000)
    assert shortage.state == "to_approve" and shortage.value == D(30000)
    # If these had been netted, the combined variance would be ~0 and neither would need approval --
    # confirm both independently do, each for its own full value.
    reqs = ApprovalRequest.objects.filter(document_type="inventory_count")
    assert reqs.count() == 2
    assert {r.amount for r in reqs} == {D(30000)}


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
    cn = billing.create_refund(inv, env.senior, reason_code="damaged_in_transit", reason="1 unit damaged")
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


def test_create_refund_requires_reason_code(env):
    # Item #32: returns reached 5.8% of sales in one company (21% in a single month at its peak,
    # per the FC review) -- a reason has to be mandatory and structured, not an optional free-text
    # afterthought, so it can actually be analysed and policed.
    inv = Invoice.objects.create(company=env.c, move_type="out_invoice", partner=env.customer, currency_id="USD",
                                 date=env.d(6, 1))
    InvoiceLine.objects.create(invoice=inv, description="Widgets", account=env.A["410101"], quantity=1,
                               unit_price=100)
    inv = billing.post_invoice(inv, env.accountant)
    with pytest.raises(BusinessError, match="reason"):
        billing.create_refund(inv, env.accountant)
    with pytest.raises(BusinessError, match="Explain"):
        billing.create_refund(inv, env.accountant, reason_code="other")
    cn = billing.create_refund(inv, env.accountant, reason_code="pricing_dispute")
    assert cn.reason_code == "pricing_dispute"


def test_large_customer_return_requires_approval(env):
    # The default "Customer return above threshold" rule (see setup_company.py) fires at 20,000 --
    # well below the 250,000 threshold on an ordinary large sales order/vendor bill, since returns
    # are a higher-risk document per the FC review.
    inv = Invoice.objects.create(company=env.c, move_type="out_invoice", partner=env.customer, currency_id="USD",
                                 date=env.d(6, 1))
    InvoiceLine.objects.create(invoice=inv, description="Equipment", account=env.A["410101"], quantity=1,
                               unit_price=30000)
    inv = billing.post_invoice(inv, env.accountant)
    assert inv.state == "posted"
    cn = billing.create_refund(inv, env.accountant, reason_code="pricing_dispute")
    cn = billing.post_invoice(cn, env.accountant)
    assert cn.state == "to_approve"
    req = ApprovalRequest.objects.get(document_id=cn.pk)
    assert req.document_type == "customer_return"
    approvals.decide(req, env.controller, True)
    cn.refresh_from_db()
    assert cn.state == "approved"
    cn = billing.post_invoice(Invoice.objects.get(pk=cn.pk), env.accountant)
    assert cn.state == "posted"


def test_vendor_bill_for_stock_product_without_po_line_is_blocked(env):
    # Item #24: a bill line for a stock product with no purchase_line has no way to raise a
    # StockMove (InvoiceLine carries no warehouse), so it used to post straight to whatever account
    # was chosen while on-hand quantity never moved -- a silent GL/stock mismatch. post_invoice()
    # must now reject it outright, whichever account the line was pointed at.
    bill = Invoice.objects.create(company=env.c, move_type="in_invoice", partner=env.vendor, currency_id="USD",
                                  date=env.d(6, 1), reference="NO-PO-1")
    InvoiceLine.objects.create(invoice=bill, product=env.fifo_prod, description="Widgets, no PO",
                               account=env.A["110301"], quantity=5, unit_price=10)
    with pytest.raises(BusinessError, match="stock product") as ex:
        billing.post_invoice(bill, env.accountant)
    assert ex.value.code == "stock_line_without_po"
    v = stock.valuation_report(env.c)
    assert not any(r["sku"] == env.fifo_prod.sku for r in v["rows"])  # nothing moved


def test_vendor_bill_for_stock_product_with_po_line_still_posts(env):
    # Same product/vendor, but properly routed through the PO + receipt flow (purchasing.create_bill
    # always sets purchase_line) -- the new check must not block the legitimate path.
    po = _po(env, qty=5, price=10)
    purchasing.confirm(po, env.accountant)
    purchasing.receive(po, env.accountant, {po.lines.first().pk: 5}, env.d(6, 2))
    bill = purchasing.create_bill(po, env.accountant, env.d(6, 3), "V-NO-PO")
    bill = billing.post_invoice(bill, env.accountant)
    assert bill.state == "posted"


def test_cancel_posted_invoice_reverses_gl(env):
    inv = Invoice.objects.create(company=env.c, move_type="out_invoice", partner=env.customer, currency_id="USD",
                                 date=env.d(6, 1))
    InvoiceLine.objects.create(invoice=inv, description="svc", account=env.A["410102"], quantity=1, unit_price=100,
                               tax=env.vat_s)
    inv = billing.post_invoice(inv, env.accountant)
    billing.cancel_invoice(inv, env.senior, "wrong customer")
    tb = reports.trial_balance(env.c, env.d(1, 1), env.d(12, 31))
    assert all(D(r["closing"]) == 0 for r in tb["rows"])


# Item #34 (Ibrahim Aly / FC review, §6 and §17(7)): "Vendor master: a change of bank details needs
# maker-checker plus an independent call-back to a known number." PartnerBankChangeRequest/
# PartnerBankVerification never touch Partner.bank_* directly -- only apply_bank_change() does,
# and only once BOTH an "approved" maker-checker decision AND a verification exist.

def test_partner_bank_fields_locked_against_plain_patch(env):
    # PartnerSerializer makes the bank fields entirely read-only -- a plain PATCH (even by someone
    # with partners.partner.manage) can never touch them, only request_bank_change()/
    # apply_bank_change() can. Exercise the model directly: a bare .save() still writes (that's
    # fine -- the serializer is the enforcement point, same as credit_limit/on_hold), but nothing
    # in the ordinary partner-update path calls it.
    assert env.vendor.bank_name == "" and env.vendor.iban == ""


def test_bank_change_requires_reason_code(env):
    with pytest.raises(BusinessError, match="reason"):
        partners.request_bank_change(env.vendor, env.accountant, iban="EG000001234567890123456789012",
                                     reason_code="")
    with pytest.raises(BusinessError, match="Explain"):
        partners.request_bank_change(env.vendor, env.accountant, iban="EG000001234567890123456789012",
                                     reason_code="other")
    req = partners.request_bank_change(env.vendor, env.accountant, iban="EG000001234567890123456789012",
                                       reason_code="other", reason="switching banks")
    # The seeded default rule matches unconditionally (min_amount=0) but still carries a non-empty
    # approver_levels=[4], so ensure_approved() opens a real ApprovalRequest and parks this at
    # "to_approve" -- it is NOT auto-approved just because the rule's threshold is 0.
    assert req.state == "to_approve"


def test_bank_change_full_maker_checker_and_verification_flow(env):
    # Full happy path: request (maker) -> decide (checker, generic approvals engine) -> verify
    # (independent callback, different person again) -> apply (writes the real Partner row).
    req = partners.request_bank_change(env.vendor, env.accountant, bank_name="New Bank",
                                       bank_account_number="12345678", iban="EG0000012345678901234567890",
                                       swift_bic="NEWBEGCX", bank_country="EG",
                                       reason_code="vendor_requested", reason="")
    assert req.state == "to_approve"
    # The real Partner row must be untouched until apply_bank_change() runs.
    env.vendor.refresh_from_db()
    assert env.vendor.bank_name == "" and env.vendor.iban == ""

    with pytest.raises(BusinessError, match="approved"):
        partners.verify_bank_change(req, env.controller, "+20 100 000 0000")  # not approved yet
    with pytest.raises(BusinessError, match="approved"):
        partners.apply_bank_change(req, env.controller)  # not approved yet

    approval_req = ApprovalRequest.objects.get(document_id=req.pk, document_type="partner_bank_change")
    with pytest.raises(BusinessError, match="Segregation"):
        approvals.decide(approval_req, env.accountant, True)  # accountant requested it
    approvals.decide(approval_req, env.controller, True)
    req.refresh_from_db()
    assert req.state == "approved"

    with pytest.raises(BusinessError, match="verification"):
        partners.apply_bank_change(req, env.controller)  # not verified yet

    with pytest.raises(BusinessError, match="Segregation"):
        partners.verify_bank_change(req, env.accountant, "+20 100 000 0000")  # accountant requested it

    with pytest.raises(BusinessError, match="phone"):
        partners.verify_bank_change(req, env.controller, "")

    partners.verify_bank_change(req, env.controller, "+20 100 000 0000", notes="called the partner's office line")
    with pytest.raises(BusinessError, match="already"):
        partners.verify_bank_change(req, env.cfo, "+20 100 000 0000")  # can't verify twice

    partner = partners.apply_bank_change(req, env.cfo)
    assert partner.bank_name == "New Bank" and partner.iban == "EG0000012345678901234567890"
    req.refresh_from_db()
    assert req.state == "done"


def test_bank_change_blocks_apply_without_approval(env):
    from apps.approvals.models import ApprovalRule
    # A two-step rule (levels [4, 5]) means the first decide() still leaves the request pending --
    # verify/apply must both refuse until the ApprovalRequest is fully approved.
    ApprovalRule.objects.filter(company=env.c, document_type="partner_bank_change").delete()
    ApprovalRule.objects.create(company=env.c, name="two-step bank change", document_type="partner_bank_change",
                                min_amount=0, approver_levels=[4, 5])
    req = partners.request_bank_change(env.vendor, env.accountant, iban="EG0000099999999999999999999",
                                       reason_code="correction", reason="")
    assert req.state == "to_approve"  # ensure_approved() returned False -- first step still pending
    with pytest.raises(BusinessError, match="approved"):
        partners.verify_bank_change(req, env.controller, "+20 100 000 0000")
    with pytest.raises(BusinessError, match="approved"):
        partners.apply_bank_change(req, env.controller)

    approval_req = ApprovalRequest.objects.get(document_id=req.pk, document_type="partner_bank_change")
    approvals.decide(approval_req, env.controller, True)  # step 1/2 only
    req.refresh_from_db()
    assert req.state == "to_approve"  # still waiting on the level-5 step
    with pytest.raises(BusinessError, match="approved"):
        partners.verify_bank_change(req, env.cfo, "+20 100 000 0000")
    with pytest.raises(BusinessError, match="approved"):
        partners.apply_bank_change(req, env.cfo)


def test_bank_change_via_generic_approvals_decide(env):
    # The maker-checker "decide" step itself goes through the fully generic apps.approvals engine,
    # not anything bank-change-specific -- same SoD rules as every other document type.
    from apps.approvals.models import ApprovalRule
    ApprovalRule.objects.filter(company=env.c, document_type="partner_bank_change").delete()
    ApprovalRule.objects.create(company=env.c, name="level-4 bank change", document_type="partner_bank_change",
                                min_amount=0, approver_levels=[4])
    req = partners.request_bank_change(env.vendor, env.accountant, iban="EG0000011111111111111111111",
                                       reason_code="bank_migration", reason="")
    assert req.state == "to_approve"
    approval_req = ApprovalRequest.objects.get(document_id=req.pk, document_type="partner_bank_change")
    with pytest.raises(BusinessError, match="Segregation"):
        approvals.decide(approval_req, env.accountant, True)  # requested it
    approvals.decide(approval_req, env.controller, True)
    req.refresh_from_db()
    assert req.state == "approved"
    partners.verify_bank_change(req, env.cfo, "+20 100 000 0000")  # cfo also holds the verify permission
    partner = partners.apply_bank_change(req, env.cfo)
    assert partner.iban == "EG0000011111111111111111111"
