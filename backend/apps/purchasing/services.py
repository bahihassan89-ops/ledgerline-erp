"""Purchasing: RFQ → purchase order (approval) → goods receipt (GRNI) → vendor bill (3-way match)."""
from django.db import transaction
from django.utils import timezone

from apps.approvals.models import DocumentType
from apps.approvals.services import ensure_approved
from apps.billing.calc import apply_totals
from apps.billing.models import DocState, Invoice, InvoiceLine, InvoiceType
from apps.billing.services import recompute
from apps.core import audit
from apps.core.exceptions import BusinessError
from apps.core.models import Sequence
from apps.core.money import D
from apps.integrations.events import emit
from apps.inventory import services as stock
from apps.inventory.models import MoveType
from apps.ledger import services as gl
from apps.sales.models import OrderState

from .models import PurchaseOrder


def recompute_order(order):
    apply_totals(order, list(order.lines.select_related("tax")), order.currency.decimals)
    order.save()
    return order


@transaction.atomic
def confirm(order, user):
    order = PurchaseOrder.objects.select_for_update().select_related("company", "partner").get(pk=order.pk)
    company, p = order.company, order.partner
    if order.state not in (OrderState.DRAFT, OrderState.APPROVED):
        raise BusinessError(f"Order cannot be confirmed from state '{order.state}'.", "invalid_state")
    if not order.lines.exists():
        raise BusinessError("Add at least one line.", "no_lines")
    if not p.is_vendor or not p.active or p.on_hold:
        raise BusinessError(f"{p.name} is not an active vendor (or is on hold).", "not_vendor")
    recompute_order(order)
    amount_base = gl.to_base(company, order.amount_total, gl.resolve_rate(company, order.currency_id, order.date))
    if not ensure_approved(company, DocumentType.PURCHASE_ORDER, order, amount_base, user):
        return order
    order.number = Sequence.next_number(company, "doc:PO", order.date.year, "PO")
    order.state = OrderState.CONFIRMED
    order.confirmed_by = user
    order.confirmed_at = timezone.now()
    order.save()
    audit.record("confirm", order, {"number": order.number, "total": order.amount_total})
    emit(company, "purchase_order.confirmed", {"id": order.pk, "number": order.number, "partner": p.code,
                                               "total": order.amount_total, "currency": order.currency_id})
    return order


@transaction.atomic
def receive(order, user, quantities=None, on_date=None):
    """Goods receipt. Stock items are valued at the PO net unit price converted at the receipt-date rate,
    posting Dr Inventory / Cr GRNI."""
    order = PurchaseOrder.objects.select_for_update().select_related("company").get(pk=order.pk)
    company = order.company
    if order.state != OrderState.CONFIRMED:
        raise BusinessError("Only confirmed orders can be received.", "invalid_state")
    on_date = on_date or timezone.localdate()
    rate = gl.resolve_rate(company, order.currency_id, on_date)
    grni = gl.require_account(gl.settings_for(company).grni_account, "GRNI")
    moves, any_qty = [], False
    for ln in order.lines.select_for_update().select_related("product", "product__category"):
        outstanding = ln.quantity - ln.qty_received
        qty = D(quantities.get(ln.pk, quantities.get(str(ln.pk), 0))) if quantities is not None else outstanding
        if qty <= 0:
            continue
        if qty > outstanding:
            raise BusinessError(f"Line {ln.product.sku}: receiving {qty} but only {outstanding} outstanding.",
                                "over_receipt")
        any_qty = True
        if ln.product.is_stock:
            unit_doc = ln.subtotal / ln.quantity
            move = stock.receive(company, ln.product, order.warehouse, qty, unit_doc * rate, on_date, user,
                                 counter_account=grni, move_type=MoveType.RECEIPT, partner=order.partner,
                                 reference=order.number, purchase_line=ln)
            ln.received_value += move.value
            moves.append(move)
        ln.qty_received += qty
        ln.save(update_fields=["qty_received", "received_value"])
    if not any_qty:
        raise BusinessError("Nothing to receive.", "nothing_to_receive")
    return moves


@transaction.atomic
def create_bill(order, user, on_date=None, vendor_reference=""):
    order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if order.state != OrderState.CONFIRMED:
        raise BusinessError("Only confirmed orders can be billed.", "invalid_state")
    if Invoice.objects.filter(purchase_order=order, state__in=[DocState.DRAFT, DocState.TO_APPROVE,
                                                               DocState.APPROVED]).exists():
        raise BusinessError("A draft bill already exists for this order.", "draft_exists")
    st = gl.settings_for(order.company)
    bill = Invoice.objects.create(company=order.company, move_type=InvoiceType.IN_INVOICE, partner=order.partner,
                                  date=on_date or timezone.localdate(), currency=order.currency,
                                  payment_term=order.payment_term or order.partner.payment_term,
                                  reference=vendor_reference or order.vendor_reference, purchase_order=order,
                                  created_by=user)
    n = 0
    for ln in order.lines.select_related("product", "product__category"):
        qty = ln.qty_received - ln.qty_billed
        if qty <= 0:
            continue
        if ln.product.is_stock:
            acc = gl.require_account(st.grni_account, "GRNI")
        else:
            acc = gl.require_account(ln.product.account("expense"), f"expense for {ln.product.sku}")
        InvoiceLine.objects.create(invoice=bill, sequence=n, product=ln.product,
                                   description=ln.description or ln.product.name, account=acc, quantity=qty,
                                   unit_price=ln.unit_price, discount_pct=ln.discount_pct, tax=ln.tax,
                                   purchase_line=ln)
        n += 1
    if not n:
        raise BusinessError("Nothing to bill: receive goods/services first.", "nothing_to_bill")
    recompute(bill)
    audit.record("create_bill", order, {"bill": bill.pk})
    return bill


@transaction.atomic
def cancel(order, user, reason=""):
    order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if order.state in (OrderState.DONE, OrderState.CANCELLED):
        raise BusinessError(f"Order is {order.state}.", "invalid_state")
    if order.lines.filter(qty_received__gt=0).exists():
        raise BusinessError("Goods were received; return them to the vendor instead.", "has_activity")
    order.state = OrderState.CANCELLED
    order.save(update_fields=["state", "updated_at"])
    audit.record("cancel", order, {"reason": reason})
    return order
