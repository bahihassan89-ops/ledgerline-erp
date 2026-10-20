"""Accounts receivable / payable: invoices, bills, credit notes, payments, allocations."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.approvals.models import DocumentType
from apps.approvals.services import ensure_approved
from apps.core import audit
from apps.core.exceptions import BusinessError
from apps.core.models import Sequence
from apps.core.money import ZERO, D, round_to
from apps.integrations.events import emit
from apps.ledger import services as gl
from apps.ledger.models import EntryState

from .calc import apply_totals
from .models import (CUSTOMER_TYPES, Allocation, DocState, Invoice, InvoiceLine, InvoiceType, Payment,
                     PaymentState)

PREFIX = {InvoiceType.OUT_INVOICE: "INV", InvoiceType.OUT_REFUND: "CN",
          InvoiceType.IN_INVOICE: "BILL", InvoiceType.IN_REFUND: "DN"}


def doc_decimals(currency):
    return currency.decimals


def receivable_account(company, partner):
    return partner.receivable_account or gl.require_account(gl.settings_for(company).receivable_account,
                                                            "accounts receivable")


def payable_account(company, partner):
    return partner.payable_account or gl.require_account(gl.settings_for(company).payable_account,
                                                         "accounts payable")


def control_account(inv):
    return receivable_account(inv.company, inv.partner) if inv.is_customer else payable_account(inv.company,
                                                                                                inv.partner)


def recompute(inv):
    lines = list(inv.lines.select_related("tax", "withholding_tax"))
    apply_totals(inv, lines, doc_decimals(inv.currency))
    if inv.state in (DocState.DRAFT, DocState.TO_APPROVE, DocState.APPROVED):
        inv.amount_residual = inv.amount_total
    inv.save()
    return inv


def _fiscal_year_of(company, on_date):
    return gl.find_period(company, on_date).fiscal_year.start_date.year


# --------------------------------------------------------------------------- invoices & bills

@transaction.atomic
def post_invoice(inv, user):
    inv = Invoice.objects.select_for_update().select_related("company", "partner", "currency").get(pk=inv.pk)
    company = inv.company
    if inv.state not in (DocState.DRAFT, DocState.APPROVED):
        raise BusinessError(f"Document cannot be posted from state '{inv.state}'.", "invalid_state")
    p = inv.partner
    if p.company_id != company.pk:
        raise BusinessError("Partner belongs to another company.", "cross_company")
    if inv.is_customer and not p.is_customer:
        raise BusinessError(f"{p.name} is not flagged as a customer.", "not_customer")
    if not inv.is_customer and not p.is_vendor:
        raise BusinessError(f"{p.name} is not flagged as a vendor.", "not_vendor")
    recompute(inv)
    lines = list(inv.lines.select_related("account", "tax", "withholding_tax", "product", "product__category",
                                          "purchase_line", "sales_line"))
    if not lines:
        raise BusinessError("Add at least one line before posting.", "no_lines")
    if inv.amount_total <= 0:
        raise BusinessError("Document total must be positive.", "zero_total")

    inv.rate = gl.resolve_rate(company, inv.currency_id, inv.date,
                               inv.rate if inv.currency_id != company.base_currency_id and inv.rate != 1 else None)
    amount_base = gl.to_base(company, inv.amount_total, inv.rate)
    doc_type = DocumentType.CUSTOMER_INVOICE if inv.is_customer else DocumentType.VENDOR_BILL
    if not inv.is_opening and not ensure_approved(company, doc_type, inv, amount_base, user):
        return inv  # now waiting for approval

    _check_taxes(inv, lines)
    if inv.move_type == InvoiceType.IN_INVOICE and not inv.is_opening:
        _three_way_match(inv, lines, user)
    if inv.is_customer and not inv.is_opening:
        from apps.einvoice.services import validate_for_eta
        validate_for_eta(inv, lines)

    # Credit/debit notes reverse the direction of the original document.
    sign = Decimal(1) if inv.move_type in (InvoiceType.OUT_INVOICE, InvoiceType.IN_REFUND) else Decimal(-1)
    # sign=+1: control account is DEBITED (customer invoice, vendor refund)

    def dc(amount):
        """+amount on the control side -> (debit, credit) in document currency"""
        a = sign * amount
        return (a, ZERO) if a > 0 else (ZERO, -a)

    if not inv.due_date:
        days = inv.payment_term.days if inv.payment_term else (p.payment_term.days if p.payment_term else 0)
        inv.due_date = inv.date + timedelta(days=days)

    specs, stock_specs = [], []
    ctrl = control_account(inv)
    d, c = dc(inv.amount_total)
    specs.append(gl.LineSpec(account=ctrl, debit=d, credit=c, partner=p, due_date=inv.due_date,
                             description=inv.reference or PREFIX[inv.move_type]))
    taxes = defaultdict(lambda: [ZERO, ZERO])  # tax -> [tax amount, base]
    whts = defaultdict(lambda: [ZERO, ZERO])
    for ln in lines:
        d, c = dc(ln.subtotal)
        spec = gl.LineSpec(account=ln.account, debit=c, credit=d, product=ln.product, quantity=ln.quantity,
                           description=ln.description, cost_center=ln.cost_center, tax=ln.tax,
                           partner=p if ln.account.reconcilable else None)
        specs.append(spec)
        if ln.tax:
            taxes[ln.tax][0] += ln.tax_amount
            taxes[ln.tax][1] += ln.subtotal
        if ln.withholding_tax and ln.wht_amount:
            whts[ln.withholding_tax][0] += ln.wht_amount
            whts[ln.withholding_tax][1] += ln.subtotal
        if inv.move_type == InvoiceType.IN_INVOICE and ln.purchase_line_id and ln.product and ln.product.is_stock:
            stock_specs.append((spec, ln))
    for tax, (amount, base) in taxes.items():
        if amount:
            d, c = dc(amount)
            specs.append(gl.LineSpec(account=tax.account, debit=c, credit=d, tax=tax,
                                     tax_base=-base if inv.is_refund else base,
                                     description=f"{tax.name}"))
    # Withholding sits on the same side as the control account: a vendor bill credits WHT payable
    # (we owe it to the Tax Authority); a customer invoice debits WHT receivable (credit against our tax).
    for tax, (amount, base) in whts.items():
        d, c = dc(amount)
        specs.append(gl.LineSpec(account=tax.account, debit=d, credit=c, tax=tax, partner=p if tax.account.reconcilable else None,
                                 tax_base=-base if inv.is_refund else base, description=f"{tax.name}"))
    specs = gl.lines_from_document_amounts(company, inv.rate, specs)
    specs.extend(_grni_clearing(company, stock_specs))

    journal = gl.journal_for(company, "sales_journal" if inv.is_customer else "purchase_journal",
                             "sales" if inv.is_customer else "purchase")
    year = _fiscal_year_of(company, inv.date)
    inv.number = Sequence.next_number(company, f"doc:{PREFIX[inv.move_type]}", year, PREFIX[inv.move_type])
    entry = gl.create_entry(company, journal, inv.date, specs, user=user, reference=f"{inv.number} {inv.reference}".strip(),
                            narration=f"{inv.get_move_type_display()} {inv.number} — {p.name}",
                            currency=inv.currency_id, rate=inv.rate, source=("invoice", inv.pk))
    gl.post_entry(entry, user, allow_control=True)

    inv.journal_entry = entry
    inv.amount_total_base = amount_base
    inv.amount_residual = inv.amount_total
    inv.payment_state = PaymentState.NOT_PAID
    inv.state = DocState.POSTED
    inv.posted_by = user
    inv.posted_at = timezone.now()
    inv.save()

    for ln in lines:
        if ln.sales_line_id:
            sl = ln.sales_line
            sl.qty_invoiced += ln.quantity if inv.move_type == InvoiceType.OUT_INVOICE else -ln.quantity
            sl.save(update_fields=["qty_invoiced"])
        if ln.purchase_line_id and inv.move_type == InvoiceType.IN_INVOICE:
            pl = ln.purchase_line
            pl.qty_billed += ln.quantity
            pl.save(update_fields=["qty_billed", "grni_cleared"])
    if inv.sales_order_id:
        from apps.sales.services import _maybe_done
        _maybe_done(inv.sales_order)
    if inv.origin_invoice_id and inv.is_refund:
        origin = inv.origin_invoice
        amt = min(origin.amount_residual, inv.amount_residual)
        if origin.state == DocState.POSTED and amt > 0 and origin.currency_id == inv.currency_id:
            allocate(inv.company, origin, amt, user, credit_note=inv, on_date=inv.date)
    audit.record("post", inv, {"number": inv.number, "total": inv.amount_total, "currency": inv.currency_id})
    if inv.return_to_stock:
        _process_return(inv, lines, user)
    from apps.einvoice import services as eta
    eta_doc = eta.queue(inv)
    if eta_doc is not None and eta.enabled_for(company).auto_submit:
        def _submit(doc_id=eta_doc.pk):
            try:
                eta.submit_pending(company, docs=list(eta.EtaDocument.objects.filter(pk=doc_id)))
            except Exception:  # network problems: the eta_sync worker retries
                import logging
                logging.getLogger("erp.eta").exception("ETA auto-submit failed")
        transaction.on_commit(_submit)
    emit(company, "invoice.posted" if inv.is_customer else "bill.posted",
         {"id": inv.pk, "number": inv.number, "type": inv.move_type, "partner": p.code, "date": inv.date,
          "due_date": inv.due_date, "currency": inv.currency_id, "total": inv.amount_total})
    return inv


def _check_taxes(inv, lines):
    scope = "sale" if inv.is_customer else "purchase"
    for ln in lines:
        if ln.tax and (ln.tax.kind != "vat" or ln.tax.scope != scope):
            raise BusinessError(f"Line '{ln.description}': '{ln.tax}' is not a {scope} VAT code.", "invalid_tax")
        if ln.withholding_tax and (ln.withholding_tax.kind != "withholding" or ln.withholding_tax.scope != scope):
            raise BusinessError(f"Line '{ln.description}': '{ln.withholding_tax}' is not a {scope} withholding code.",
                                "invalid_tax")


def _three_way_match(inv, lines, user):
    """Vendor bill vs. purchase order vs. goods receipt."""
    company = inv.company
    tol = D(company.bill_price_tolerance_pct)
    problems, seen = [], set()
    for ln in lines:
        pl = ln.purchase_line
        if not pl:
            continue
        if pl.pk in seen:
            problems.append(f"Line '{ln.description}': the same PO line appears twice on this bill.")
            continue
        seen.add(pl.pk)
        if pl.order.company_id != company.pk or pl.order.partner_id != inv.partner_id:
            problems.append(f"Line '{ln.description}': PO line belongs to another vendor/company.")
            continue
        already = pl.qty_billed
        if ln.product and ln.product.is_stock and already + ln.quantity > pl.qty_received:
            problems.append(f"Line '{ln.description}': billing {ln.quantity} but only "
                            f"{pl.qty_received - already} received and not yet billed.")
        elif already + ln.quantity > pl.quantity:
            problems.append(f"Line '{ln.description}': billed quantity exceeds ordered quantity.")
        po_net = pl.subtotal / pl.quantity
        bill_net = ln.subtotal / ln.quantity
        if bill_net > po_net * (1 + tol / 100) and user.level_for(company.pk) < 4:
            problems.append(f"Line '{ln.description}': unit price {bill_net:.4f} exceeds PO price {po_net:.4f} "
                            f"beyond the {tol}% tolerance. A Financial Controller (level 4+) must post this bill.")
    if problems:
        raise BusinessError("Three-way match failed.", "three_way_match", {"errors": problems})


def _grni_clearing(company, stock_specs):
    """For stocked PO lines: the bill debits GRNI at the *receipt* value; any difference between
    bill price and receipt cost goes to Purchase Price Variance."""
    extra = []
    st = gl.settings_for(company)
    for spec, ln in stock_specs:
        pl = ln.purchase_line
        grni = gl.require_account(st.grni_account, "GRNI (goods received not invoiced)")
        if pl.qty_billed + ln.quantity >= pl.qty_received:
            clear = pl.received_value - pl.grni_cleared  # last bill clears the remainder exactly
        else:
            clear = round_to(pl.received_value * ln.quantity / pl.qty_received, gl.base_decimals(company))
        pl.grni_cleared += clear
        bill_base = spec.debit - spec.credit
        spec.account = grni
        spec.debit, spec.credit = (clear, ZERO) if clear >= 0 else (ZERO, -clear)
        variance = bill_base - clear
        if variance:
            pv = gl.require_account(st.price_variance_account, "purchase price variance")
            extra.append(gl.LineSpec(account=pv, debit=variance if variance > 0 else ZERO,
                                     credit=-variance if variance < 0 else ZERO, amount_currency=ZERO,
                                     product=ln.product, description=f"Price variance {ln.description}"))
    return extra


@transaction.atomic
def cancel_invoice(inv, user, reason=""):
    inv = Invoice.objects.select_for_update().get(pk=inv.pk)
    if inv.state == DocState.CANCELLED:
        raise BusinessError("Already cancelled.", "invalid_state")
    if inv.state == DocState.POSTED:
        if inv.allocations.exists() or inv.credit_allocations.exists():
            raise BusinessError("This document has payments or credit notes allocated. Remove the allocations "
                                "or issue a credit note instead.", "has_allocations")
        if inv.move_type == InvoiceType.IN_INVOICE and inv.lines.filter(purchase_line__isnull=False).exists():
            raise BusinessError("Bills matched to purchase orders cannot be cancelled; issue a vendor debit note.",
                                "matched_bill")
        gl.reverse_entry(inv.journal_entry, user, reason=reason or f"Cancel {inv.number}", allow_source=True)
        for ln in inv.lines.select_related("sales_line"):
            if ln.sales_line_id:
                ln.sales_line.qty_invoiced -= ln.quantity if inv.move_type == InvoiceType.OUT_INVOICE else -ln.quantity
                ln.sales_line.save(update_fields=["qty_invoiced"])
    inv.state = DocState.CANCELLED
    inv.amount_residual = ZERO
    inv.save()
    audit.record("cancel", inv, {"reason": reason})
    return inv


def _process_return(inv, lines, user):
    """Goods physically returned on a credit/debit note.
    Customer return: stock comes back at its original delivery cost   Dr Inventory / Cr COGS.
    Vendor return: stock leaves at current cost against GRNI; the gap between the debit-note price and
    that cost is moved from GRNI to purchase price variance."""
    from apps.inventory import services as stock
    from apps.inventory.models import MoveType, StockMove
    company = inv.company
    st = gl.settings_for(company)
    for ln in lines:
        if not (ln.product and ln.product.is_stock):
            continue
        if inv.move_type == InvoiceType.OUT_REFUND:
            sl = ln.sales_line
            if not sl:
                raise BusinessError(f"Line '{ln.description}': returns to stock need the original sales order line.",
                                    "return_without_order")
            if ln.quantity > sl.qty_delivered:
                raise BusinessError(f"Line '{ln.description}': returning {ln.quantity} but only {sl.qty_delivered} "
                                    f"were delivered.", "over_return")
            agg = StockMove.objects.filter(sales_line=sl, move_type=MoveType.DELIVERY).aggregate(
                v=Sum("value"), q=Sum("quantity"))
            unit = (agg["v"] or ZERO) / agg["q"] if agg["q"] else ln.product.purchase_price
            cogs = gl.require_account(ln.product.category.cogs_account, "COGS")
            stock.receive(company, ln.product, sl.order.warehouse, ln.quantity, unit, inv.date, user,
                          counter_account=cogs, move_type=MoveType.CUSTOMER_RETURN, partner=inv.partner,
                          reference=inv.number, sales_line=sl)
            sl.qty_delivered -= ln.quantity
            sl.save(update_fields=["qty_delivered"])
        elif inv.move_type == InvoiceType.IN_REFUND:
            pl = ln.purchase_line
            grni = gl.require_account(st.grni_account, "GRNI")
            if not pl or ln.account_id != grni.pk:
                raise BusinessError(f"Line '{ln.description}': vendor returns to stock must reference the PO line "
                                    f"and use the GRNI account.", "return_without_order")
            if ln.quantity > pl.qty_received:
                raise BusinessError(f"Line '{ln.description}': returning more than was received.", "over_return")
            move = stock.issue(company, ln.product, pl.order.warehouse, ln.quantity, inv.date, user,
                               counter_account=grni, move_type=MoveType.VENDOR_RETURN, partner=inv.partner,
                               reference=inv.number, purchase_line=pl)
            diff = gl.to_base(company, ln.subtotal, inv.rate) - move.value
            if diff:
                pv = gl.require_account(st.price_variance_account, "purchase price variance")
                e = gl.create_entry(company, gl.journal_for(company, "inventory_journal", "inventory"), inv.date, [
                    gl.LineSpec(account=grni, debit=diff if diff > 0 else ZERO, credit=-diff if diff < 0 else ZERO,
                                description=f"Return price difference {ln.product.sku}"),
                    gl.LineSpec(account=pv, debit=-diff if diff < 0 else ZERO, credit=diff if diff > 0 else ZERO,
                                description=f"Return price difference {ln.product.sku}"),
                ], user=user, reference=inv.number, source=("invoice", inv.pk))
                gl.post_entry(e, user, allow_control=True)
            pl.qty_received -= ln.quantity
            pl.qty_billed = max(ZERO, pl.qty_billed - ln.quantity)
            pl.received_value -= move.value
            pl.grni_cleared -= move.value
            pl.save(update_fields=["qty_received", "qty_billed", "received_value", "grni_cleared"])


@transaction.atomic
def create_refund(inv, user, lines=None, reason="", return_to_stock=False):
    """Draft credit note (customer) or debit note (vendor) from a posted document.
    `return_to_stock`: the goods come back physically (stock moves are created when it is posted)."""
    if inv.state != DocState.POSTED or inv.is_refund:
        raise BusinessError("Credit/debit notes can only be issued against a posted invoice or bill.", "invalid_state")
    refund = Invoice.objects.create(
        company=inv.company, move_type=InvoiceType.OUT_REFUND if inv.is_customer else InvoiceType.IN_REFUND,
        partner=inv.partner, date=timezone.localdate(), currency=inv.currency, rate=inv.rate,
        reference=f"Refund of {inv.number}", origin_invoice=inv, notes=reason, created_by=user,
        sales_order=inv.sales_order, purchase_order=inv.purchase_order, return_to_stock=return_to_stock)
    for i, ln in enumerate(inv.lines.all()):
        InvoiceLine.objects.create(invoice=refund, sequence=i, product=ln.product, description=ln.description,
                                   account=ln.account, quantity=ln.quantity, unit_price=ln.unit_price,
                                   discount_pct=ln.discount_pct, tax=ln.tax, withholding_tax=ln.withholding_tax,
                                   cost_center=ln.cost_center, sales_line=ln.sales_line,
                                   purchase_line=ln.purchase_line)
    recompute(refund)
    audit.record("create_refund", refund, {"origin": inv.number})
    return refund


# --------------------------------------------------------------------------- payments

@transaction.atomic
def post_payment(pay, user, allocations=()):
    pay = Payment.objects.select_for_update().select_related("company", "partner", "journal").get(pk=pay.pk)
    company = pay.company
    if pay.state not in (DocState.DRAFT, DocState.APPROVED):
        raise BusinessError(f"Payment cannot be posted from state '{pay.state}'.", "invalid_state")
    j = pay.journal
    if j.company_id != company.pk or j.type not in ("bank", "cash") or not j.default_account_id:
        raise BusinessError("Choose a bank or cash journal with a default account.", "invalid_journal")
    if j.currency_id and j.currency_id != pay.currency_id:
        raise BusinessError(f"Journal {j.code} only accepts {j.currency_id}.", "currency_mismatch")
    p = pay.partner
    if pay.direction == Payment.Direction.INBOUND and not p.is_customer:
        raise BusinessError(f"{p.name} is not a customer.", "not_customer")
    if pay.direction == Payment.Direction.OUTBOUND and not p.is_vendor:
        raise BusinessError(f"{p.name} is not a vendor.", "not_vendor")
    pay.rate = gl.resolve_rate(company, pay.currency_id, pay.date,
                               pay.rate if pay.currency_id != company.base_currency_id and pay.rate != 1 else None)
    if not ensure_approved(company, DocumentType.PAYMENT, pay, gl.to_base(company, pay.amount, pay.rate), user):
        return pay
    inbound = pay.direction == Payment.Direction.INBOUND
    ctrl = receivable_account(company, p) if inbound else payable_account(company, p)
    bank = j.default_account
    if pay.method == "cheque":
        if not pay.cheque_number or not pay.cheque_due_date:
            raise BusinessError("Cheque number and due date are required.", "cheque_details")
        st = gl.settings_for(company)
        bank = gl.require_account(st.cheques_receivable_account if inbound else st.cheques_payable_account,
                                  "cheques receivable" if inbound else "cheques payable")
        pay.cheque_state = "in_hand" if inbound else "issued"
    specs = [
        gl.LineSpec(account=bank, debit=pay.amount if inbound else ZERO, credit=ZERO if inbound else pay.amount,
                    description=pay.reference or pay.memo),
        gl.LineSpec(account=ctrl, debit=ZERO if inbound else pay.amount, credit=pay.amount if inbound else ZERO,
                    partner=p, description=pay.memo or pay.reference),
    ]
    specs = gl.lines_from_document_amounts(company, pay.rate, specs)
    prefix = "RCPT" if inbound else "PAY"
    pay.number = Sequence.next_number(company, f"doc:{prefix}", _fiscal_year_of(company, pay.date), prefix)
    entry = gl.create_entry(company, j, pay.date, specs, user=user, reference=f"{pay.number} {pay.reference}".strip(),
                            narration=f"{pay.get_direction_display()} {pay.number} — {p.name}",
                            currency=pay.currency_id, rate=pay.rate, source=("payment", pay.pk))
    gl.post_entry(entry, user, allow_control=True)
    pay.journal_entry = entry
    pay.state = DocState.POSTED
    pay.amount_unallocated = pay.amount
    pay.posted_by = user
    pay.posted_at = timezone.now()
    pay.save()
    audit.record("post", pay, {"number": pay.number, "amount": pay.amount, "currency": pay.currency_id})
    emit(company, "payment.posted", {"id": pay.pk, "number": pay.number, "direction": pay.direction,
                                     "partner": p.code, "amount": pay.amount, "currency": pay.currency_id,
                                     "date": pay.date})
    for inv, amount in allocations:
        allocate(company, inv, amount, user, payment=pay, on_date=pay.date)
    return pay


@transaction.atomic
def cancel_payment(pay, user, reason=""):
    pay = Payment.objects.select_for_update().get(pk=pay.pk)
    if pay.state == DocState.POSTED:
        if pay.allocations.exists():
            raise BusinessError("Remove the payment's allocations first.", "has_allocations")
        gl.reverse_entry(pay.journal_entry, user, reason=reason or f"Cancel {pay.number}", allow_source=True)
    elif pay.state == DocState.CANCELLED:
        raise BusinessError("Already cancelled.", "invalid_state")
    pay.state = DocState.CANCELLED
    pay.amount_unallocated = ZERO
    pay.save()
    audit.record("cancel", pay, {"reason": reason})
    return pay


# --------------------------------------------------------------------------- cheques

@transaction.atomic
def cheque_deposit(pay, user, on_date=None):
    pay = Payment.objects.select_for_update().get(pk=pay.pk)
    if pay.method != "cheque" or pay.direction != "inbound" or pay.cheque_state != "in_hand":
        raise BusinessError("Only received cheques in hand can be deposited.", "invalid_state")
    pay.cheque_state = "deposited"
    pay.save(update_fields=["cheque_state", "updated_at"])
    audit.record("cheque_deposit", pay, {"date": on_date})
    return pay


@transaction.atomic
def cheque_clear(pay, user, on_date):
    """The bank collected / paid the cheque: move it from the cheques account to the bank account."""
    pay = Payment.objects.select_for_update().select_related("journal", "company").get(pk=pay.pk)
    company = pay.company
    if pay.method != "cheque" or pay.state != DocState.POSTED or pay.cheque_state not in ("in_hand", "deposited",
                                                                                           "issued"):
        raise BusinessError("This cheque cannot be cleared in its current state.", "invalid_state")
    st = gl.settings_for(company)
    bank = pay.journal.default_account
    inbound = pay.direction == "inbound"
    chq = st.cheques_receivable_account if inbound else st.cheques_payable_account
    desc = f"Cheque {pay.cheque_number} cleared — {pay.partner.name}"
    specs = [gl.LineSpec(account=bank, debit=pay.amount if inbound else ZERO, credit=ZERO if inbound else pay.amount,
                         description=desc),
             gl.LineSpec(account=chq, debit=ZERO if inbound else pay.amount, credit=pay.amount if inbound else ZERO,
                         description=desc)]
    rate = gl.resolve_rate(company, pay.currency_id, on_date)
    specs = gl.lines_from_document_amounts(company, rate, specs)
    # the cheque account must clear at the rate it was booked at; any FX difference goes to realised FX
    booked = gl.to_base(company, pay.amount, pay.rate)
    chq_spec = specs[1]
    now_base = chq_spec.debit + chq_spec.credit
    if now_base != booked:
        diff = now_base - booked
        if inbound:
            chq_spec.credit = booked
        else:
            chq_spec.debit = booked
        gain = diff > 0 if inbound else diff < 0
        fx = gl.require_account(st.fx_gain_account if gain else st.fx_loss_account, "FX")
        specs.append(gl.LineSpec(account=fx, debit=ZERO if gain else abs(diff), credit=abs(diff) if gain else ZERO,
                                 amount_currency=ZERO, description="FX difference on cheque"))
    e = gl.create_entry(company, pay.journal, on_date, specs, user=user, reference=f"CHQ {pay.cheque_number}",
                        narration=desc, currency=pay.currency_id, rate=rate, source=("payment", pay.pk))
    gl.post_entry(e, user, allow_control=True)
    pay.cheque_state, pay.cheque_cleared_entry = "cleared", e
    pay.save(update_fields=["cheque_state", "cheque_cleared_entry", "updated_at"])
    audit.record("cheque_clear", pay, {"entry": e.number})
    return pay


@transaction.atomic
def cheque_bounce(pay, user, reason):
    """Returned unpaid (or a stopped issued cheque): reopen the invoices and reverse the payment."""
    pay = Payment.objects.select_for_update().get(pk=pay.pk)
    if pay.method != "cheque" or pay.cheque_state not in ("in_hand", "deposited", "issued"):
        raise BusinessError("Only uncleared cheques can bounce.", "invalid_state")
    if not reason:
        raise BusinessError("A reason is required.", "reason_required")
    for a in list(pay.allocations.all()):
        remove_allocation(a, user)
    cancel_payment(pay, user, reason=f"Cheque {pay.cheque_number} returned: {reason}")
    pay.refresh_from_db()
    pay.cheque_state = "bounced"
    pay.save(update_fields=["cheque_state", "updated_at"])
    audit.record("cheque_bounce", pay, {"reason": reason})
    return pay


def cheque_register(company, direction=None, state=None):
    qs = Payment.objects.filter(company=company, method="cheque").select_related("partner", "journal")
    if direction:
        qs = qs.filter(direction=direction)
    if state:
        qs = qs.filter(cheque_state=state)
    today = timezone.localdate()
    rows = [{"id": p.pk, "number": p.number, "direction": p.direction, "partner": p.partner.name,
             "cheque_number": p.cheque_number, "bank": p.cheque_bank, "due_date": p.cheque_due_date,
             "amount": str(p.amount), "currency": p.currency_id, "state": p.cheque_state, "journal": p.journal.code,
             "overdue": bool(p.cheque_due_date and p.cheque_due_date < today and
                             p.cheque_state in ("in_hand", "deposited", "issued"))}
            for p in qs.order_by("cheque_due_date")]
    return {"rows": rows}


# --------------------------------------------------------------------------- allocation

COMPATIBLE = {
    ("inbound", InvoiceType.OUT_INVOICE), ("outbound", InvoiceType.IN_INVOICE),
    # refunds paid out / received back
    ("outbound", InvoiceType.OUT_REFUND), ("inbound", InvoiceType.IN_REFUND),
}
NOTE_FOR = {InvoiceType.OUT_INVOICE: InvoiceType.OUT_REFUND, InvoiceType.IN_INVOICE: InvoiceType.IN_REFUND}


def _set_payment_state(inv):
    if inv.amount_residual == 0:
        inv.payment_state = PaymentState.PAID
    elif inv.amount_residual < inv.amount_total:
        inv.payment_state = PaymentState.PARTIAL
    else:
        inv.payment_state = PaymentState.NOT_PAID


@transaction.atomic
def allocate(company, inv, amount, user, *, payment=None, credit_note=None, on_date=None):
    """Settle `amount` (invoice currency) of `inv` with a payment or a credit note.
    Posts realised FX gain/loss when rates differ."""
    amount = D(amount)
    inv = Invoice.objects.select_for_update().get(pk=inv.pk)
    if amount <= 0:
        raise BusinessError("Allocation amount must be positive.", "invalid_amount")
    if inv.company_id != company.pk or inv.state != DocState.POSTED:
        raise BusinessError("Invoice must be posted and belong to this company.", "invalid_invoice")
    if amount > inv.amount_residual:
        raise BusinessError(f"Amount {amount} exceeds the open balance {inv.amount_residual} of {inv.number}.",
                            "over_allocation")
    if payment is not None:
        src = Payment.objects.select_for_update().get(pk=payment.pk)
        if (src.direction, inv.move_type) not in COMPATIBLE:
            raise BusinessError("This payment direction cannot settle this document type.", "incompatible")
        available, src_rate = src.amount_unallocated, src.rate
    else:
        src = Invoice.objects.select_for_update().get(pk=credit_note.pk)
        if NOTE_FOR.get(inv.move_type) != src.move_type:
            raise BusinessError("This credit/debit note cannot settle this document type.", "incompatible")
        available, src_rate = src.amount_residual, src.rate
    if src.company_id != company.pk or src.state != DocState.POSTED:
        raise BusinessError("Payment / credit note must be posted.", "invalid_source")
    if src.partner_id != inv.partner_id:
        raise BusinessError("Payment and invoice must belong to the same partner.", "partner_mismatch")
    if src.currency_id != inv.currency_id:
        raise BusinessError("Payment and invoice must be in the same currency.", "currency_mismatch")
    if amount > available:
        raise BusinessError(f"Amount {amount} exceeds the available {available}.", "over_allocation")
    on_date = on_date or timezone.localdate()

    fx_entry = None
    if src_rate != inv.rate:
        fx_entry = _post_fx(company, inv, amount, src_rate, on_date, user)

    alloc = Allocation.objects.create(company=company, invoice=inv, payment=payment if payment else None,
                                      credit_note=src if credit_note else None, amount=amount, date=on_date,
                                      fx_entry=fx_entry, created_by=user)
    inv.amount_residual -= amount
    _set_payment_state(inv)
    inv.save(update_fields=["amount_residual", "payment_state", "updated_at"])
    if payment is not None:
        src.amount_unallocated -= amount
        src.save(update_fields=["amount_unallocated", "updated_at"])
    else:
        src.amount_residual -= amount
        _set_payment_state(src)
        src.save(update_fields=["amount_residual", "payment_state", "updated_at"])
    audit.record("allocate", inv, {"amount": amount, "source": str(src), "fx_entry": fx_entry.number if fx_entry else None})
    if inv.payment_state == PaymentState.PAID:
        emit(company, "invoice.paid" if inv.is_customer else "bill.paid",
             {"id": inv.pk, "number": inv.number, "partner": inv.partner.code})
    return alloc


def _post_fx(company, inv, amount, src_rate, on_date, user):
    """Realised exchange difference on the control account.
    Customer invoice: receiving at a higher rate than invoiced = gain. Vendor bill: paying at a higher rate = loss."""
    inv_base = gl.to_base(company, amount, inv.rate)
    src_base = gl.to_base(company, amount, src_rate)
    diff = src_base - inv_base
    if not diff:
        return None
    st = gl.settings_for(company)
    ctrl = control_account(inv)
    # Is the control account debit-side for this document? (customer invoice / vendor refund)
    debit_side = inv.move_type in (InvoiceType.OUT_INVOICE, InvoiceType.IN_REFUND)
    # The settlement cleared `src_base` from the control account while the document put `inv_base`
    # there. Residual on control = inv_base - src_base (debit_side) -> fix it.
    correction = diff if debit_side else -diff   # + = debit control
    # Debiting the control account means crediting the FX account, i.e. a gain.
    gain = correction > 0
    if correction > 0:
        ctrl_spec = gl.LineSpec(account=ctrl, debit=correction, partner=inv.partner, amount_currency=ZERO)
    else:
        ctrl_spec = gl.LineSpec(account=ctrl, credit=-correction, partner=inv.partner, amount_currency=ZERO)
    fx_acc = gl.require_account(st.fx_gain_account if gain else st.fx_loss_account,
                                "FX gain" if gain else "FX loss")
    other = gl.LineSpec(account=fx_acc, debit=ctrl_spec.credit, credit=ctrl_spec.debit, amount_currency=ZERO,
                        description=f"Realised FX {'gain' if gain else 'loss'} on {inv.number}")
    ctrl_spec.description = other.description
    journal = gl.journal_for(company, "general_journal", "general")
    entry = gl.create_entry(company, journal, on_date, [ctrl_spec, other], user=user,
                            reference=f"FX {inv.number}", narration=other.description,
                            currency=inv.currency_id, rate=src_rate, source=("fx_allocation", inv.pk))
    gl.post_entry(entry, user, allow_control=True)
    return entry


@transaction.atomic
def remove_allocation(alloc, user):
    alloc = Allocation.objects.select_for_update().get(pk=alloc.pk)
    inv = Invoice.objects.select_for_update().get(pk=alloc.invoice_id)
    if alloc.fx_entry_id and alloc.fx_entry.state == EntryState.POSTED:
        gl.reverse_entry(alloc.fx_entry, user, reason=f"Unallocate {inv.number}", allow_source=True)
    inv.amount_residual += alloc.amount
    _set_payment_state(inv)
    inv.save(update_fields=["amount_residual", "payment_state", "updated_at"])
    if alloc.payment_id:
        pay = Payment.objects.select_for_update().get(pk=alloc.payment_id)
        pay.amount_unallocated += alloc.amount
        pay.save(update_fields=["amount_unallocated", "updated_at"])
    else:
        cn = Invoice.objects.select_for_update().get(pk=alloc.credit_note_id)
        cn.amount_residual += alloc.amount
        _set_payment_state(cn)
        cn.save(update_fields=["amount_residual", "payment_state", "updated_at"])
    audit.record("unallocate", inv, {"amount": alloc.amount, "allocation": alloc.pk})
    alloc.delete()


# --------------------------------------------------------------------------- reports

BUCKETS = [("current", None, 0), ("1_30", 1, 30), ("31_60", 31, 60), ("61_90", 61, 90), ("over_90", 91, None)]


def aging(company, kind, as_of):
    """Open items at `as_of` (only allocations dated on/before as_of count), by days overdue.
    kind: 'receivable' | 'payable'. Amounts in base currency at document rate."""
    types = [InvoiceType.OUT_INVOICE, InvoiceType.OUT_REFUND] if kind == "receivable" else \
        [InvoiceType.IN_INVOICE, InvoiceType.IN_REFUND]
    invs = (Invoice.objects.filter(company=company, state=DocState.POSTED, move_type__in=types, date__lte=as_of)
            .select_related("partner"))
    alloc_inv = dict(Allocation.objects.filter(company=company, date__lte=as_of, invoice__in=invs)
                     .values_list("invoice").annotate(s=Sum("amount")))
    alloc_cn = dict(Allocation.objects.filter(company=company, date__lte=as_of, credit_note__in=invs)
                    .values_list("credit_note").annotate(s=Sum("amount")))
    partners = {}
    decimals = company.base_currency.decimals
    for inv in invs:
        open_amt = inv.amount_total - (alloc_inv.get(inv.pk) or ZERO) - (alloc_cn.get(inv.pk) or ZERO)
        if open_amt <= 0:
            continue
        base = round_to(open_amt * inv.rate, decimals)
        if inv.is_refund:
            base = -base
        days = (as_of - (inv.due_date or inv.date)).days
        bucket = next(b for b, lo, hi in BUCKETS if (lo is None and days <= 0) or
                      (lo is not None and days >= lo and (hi is None or days <= hi)))
        row = partners.setdefault(inv.partner_id, {"partner_id": inv.partner_id, "partner": inv.partner.name,
                                                   "code": inv.partner.code, **{b: ZERO for b, *_ in BUCKETS},
                                                   "total": ZERO, "documents": []})
        row[bucket] += base
        row["total"] += base
        row["documents"].append({"id": inv.pk, "number": inv.number, "date": inv.date, "due_date": inv.due_date,
                                 "days_overdue": max(days, 0), "currency": inv.currency_id,
                                 "open_amount": str(open_amt), "open_base": str(base), "bucket": bucket})
    # unallocated payments reduce the balance
    pays = Payment.objects.filter(company=company, state=DocState.POSTED, date__lte=as_of,
                                  direction="inbound" if kind == "receivable" else "outbound")
    pay_alloc = dict(Allocation.objects.filter(company=company, date__lte=as_of, payment__in=pays)
                     .values_list("payment").annotate(s=Sum("amount")))
    for pay in pays.select_related("partner"):
        unalloc = pay.amount - (pay_alloc.get(pay.pk) or ZERO)
        if unalloc <= 0:
            continue
        base = round_to(unalloc * pay.rate, decimals)
        row = partners.setdefault(pay.partner_id, {"partner_id": pay.partner_id, "partner": pay.partner.name,
                                                   "code": pay.partner.code, **{b: ZERO for b, *_ in BUCKETS},
                                                   "total": ZERO, "documents": []})
        row.setdefault("unallocated", ZERO)
        row["unallocated"] = row.get("unallocated", ZERO) + base
        row["total"] -= base
    rows = sorted(partners.values(), key=lambda r: -r["total"])
    totals = {b: sum((r[b] for r in rows), ZERO) for b, *_ in BUCKETS}
    totals["unallocated"] = sum((r.get("unallocated", ZERO) for r in rows), ZERO)
    totals["total"] = sum((r["total"] for r in rows), ZERO)

    def s(r):
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in r.items()}
    return {"as_of": as_of, "kind": kind, "buckets": [b for b, *_ in BUCKETS], "rows": [s(r) for r in rows],
            "totals": s(totals)}


def subledger_reconciliation(company, as_of):
    """Proves AR/AP sub-ledger (open documents) equals the GL control accounts."""
    from apps.ledger.models import JournalLine
    st = gl.settings_for(company)
    out = {}
    for kind, acc_type in (("receivable", "receivable"), ("payable", "payable")):
        ag = aging(company, kind, as_of)
        sub = D(ag["totals"]["total"])
        gl_bal = JournalLine.objects.filter(company=company, posted=True, date__lte=as_of,
                                            account__account_type=acc_type).exclude(
            entry__source_type="fx_revaluation").aggregate(
            d=Sum("debit"), c=Sum("credit"))
        bal = (gl_bal["d"] or ZERO) - (gl_bal["c"] or ZERO)
        if kind == "payable":
            bal = -bal
        out[kind] = {"subledger": str(sub), "general_ledger": str(bal), "difference": str(bal - sub),
                     "reconciled": bal == sub}
    return {"as_of": as_of, **out}


def withholding_report(company, date_from, date_to, scope="purchase"):
    """scope=purchase: tax we withheld from vendors — Form 41 (quarterly, with the payment to the ETA).
    scope=sale: tax customers withheld from us — collect their certificates; credit against income tax."""
    from apps.ledger.models import JournalLine
    types = ["in_invoice", "in_refund"] if scope == "purchase" else ["out_invoice", "out_refund"]
    lines = (InvoiceLine.objects.filter(invoice__company=company, invoice__state=DocState.POSTED,
                                        invoice__move_type__in=types, invoice__date__gte=date_from,
                                        invoice__date__lte=date_to, wht_amount__gt=0)
             .select_related("invoice", "invoice__partner", "withholding_tax"))
    rows = {}
    for ln in lines:
        inv = ln.invoice
        sign = -1 if inv.is_refund else 1
        key = (inv.partner_id, ln.withholding_tax_id)
        r = rows.setdefault(key, {"partner": inv.partner.name, "partner_code": inv.partner.code,
                                  "tax_number": inv.partner.tax_number, "national_id": inv.partner.national_id,
                                  "tax": ln.withholding_tax.name, "eta_subtype": ln.withholding_tax.eta_subtype,
                                  "rate": str(ln.withholding_tax.rate), "base": ZERO, "withheld": ZERO,
                                  "documents": []})
        r["base"] += sign * gl.to_base(company, ln.subtotal, inv.rate)
        r["withheld"] += sign * gl.to_base(company, ln.wht_amount, inv.rate)
        if inv.number not in r["documents"]:
            r["documents"].append(inv.number)
    out = sorted(rows.values(), key=lambda r: r["partner"])
    total = sum((r["withheld"] for r in out), ZERO)
    acc_type = "wht_payable" if scope == "purchase" else "wht_receivable"
    agg = JournalLine.objects.filter(company=company, posted=True, date__gte=date_from, date__lte=date_to,
                                     account__account_type=acc_type).aggregate(d=Sum("debit"), c=Sum("credit"))
    gl_move = (agg["c"] or ZERO) - (agg["d"] or ZERO)
    if scope == "sale":
        gl_move = -gl_move
    for r in out:
        r["base"], r["withheld"] = str(r["base"]), str(r["withheld"])
    return {"scope": scope, "date_from": date_from, "date_to": date_to, "rows": out, "total_withheld": str(total),
            "gl_movement": str(gl_move),
            "note": ("Form 41: submit quarterly with payment to the Tax Authority by the end of the month following "
                     "the quarter. Only payments to a vendor above EGP 300 are subject (verify current rules).")
            if scope == "purchase" else "Request withholding certificates from customers; credit them against "
                                        "corporate income tax."}


def vat_return(company, date_from, date_to):
    """Egyptian VAT return (Law 67/2016) working paper: sales and purchases by tax rate/type."""
    from apps.ledger.models import Tax
    rows = []
    totals = {"output": ZERO, "input": ZERO}
    for scope in ("sale", "purchase"):
        types = ["out_invoice", "out_refund"] if scope == "sale" else ["in_invoice", "in_refund"]
        for tax in Tax.objects.filter(company=company, scope=scope, kind="vat"):
            agg = defaultdict(lambda: ZERO)
            for ln in InvoiceLine.objects.filter(invoice__company=company, invoice__state=DocState.POSTED,
                                                 invoice__move_type__in=types, invoice__date__gte=date_from,
                                                 invoice__date__lte=date_to, tax=tax).select_related("invoice"):
                s = -1 if ln.invoice.is_refund else 1
                agg["base"] += s * gl.to_base(company, ln.subtotal, ln.invoice.rate)
                agg["tax"] += s * gl.to_base(company, ln.tax_amount, ln.invoice.rate)
            if agg["base"] or agg["tax"]:
                rows.append({"scope": scope, "code": tax.code, "name": tax.name, "rate": str(tax.rate),
                             "eta": f"{tax.eta_type}/{tax.eta_subtype}".strip("/"), "base": str(agg["base"]),
                             "tax": str(agg["tax"])})
                totals["output" if scope == "sale" else "input"] += agg["tax"]
    net = totals["output"] - totals["input"]
    return {"date_from": date_from, "date_to": date_to, "rows": rows, "output_vat": str(totals["output"]),
            "input_vat": str(totals["input"]), "net_vat": str(net),
            "position": "payable" if net >= 0 else "credit carried forward",
            "note": "Monthly return. Input VAT is deductible only with a valid tax invoice (e-invoice on ETA). "
                    "Verify the filing deadline and form with your tax advisor."}
