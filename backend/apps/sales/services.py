"""Sales: quotation → order (credit check, approval) → delivery (COGS) → invoice."""
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from apps.approvals.models import DocumentType
from apps.approvals.services import ensure_approved
from apps.billing.calc import apply_totals
from apps.billing.models import DocState, Invoice, InvoiceLine, InvoiceType
from apps.billing.services import recompute
from apps.core import audit
from apps.core.exceptions import BusinessError
from apps.core.models import Sequence
from apps.core.money import ZERO, D, round_to
from apps.integrations.events import emit
from apps.inventory import services as stock
from apps.inventory.models import MoveType
from apps.ledger import services as gl

from .models import OrderState, SalesOrder


def recompute_order(order):
    apply_totals(order, list(order.lines.select_related("tax")), order.currency.decimals)
    order.save()
    return order


def credit_exposure(company, partner):
    """Open receivables + confirmed-but-not-invoiced orders, base currency."""
    open_inv = ZERO
    for inv in Invoice.objects.filter(company=company, partner=partner, state=DocState.POSTED,
                                      move_type__in=[InvoiceType.OUT_INVOICE, InvoiceType.OUT_REFUND]):
        amt = round_to(inv.amount_residual * inv.rate, company.base_currency.decimals)
        open_inv += -amt if inv.move_type == InvoiceType.OUT_REFUND else amt
    uninvoiced = ZERO
    orders = SalesOrder.objects.filter(company=company, partner=partner,
                                       state=OrderState.CONFIRMED).prefetch_related("lines")
    for so in orders:
        rate = gl.resolve_rate(company, so.currency_id, so.date)
        for ln in so.lines.all():
            remaining = ln.quantity - ln.qty_invoiced
            if remaining > 0:
                uninvoiced += gl.to_base(company, ln.total * remaining / ln.quantity, rate)
    return open_inv + uninvoiced


@transaction.atomic
def confirm(order, user):
    order = SalesOrder.objects.select_for_update().select_related("company", "partner").get(pk=order.pk)
    company, p = order.company, order.partner
    if order.state not in (OrderState.DRAFT, OrderState.APPROVED):
        raise BusinessError(f"Order cannot be confirmed from state '{order.state}'.", "invalid_state")
    if not order.lines.exists():
        raise BusinessError("Add at least one line.", "no_lines")
    if not p.is_customer or not p.active:
        raise BusinessError(f"{p.name} is not an active customer.", "not_customer")
    if p.on_hold:
        raise BusinessError(f"{p.name} is on hold.", "partner_on_hold")
    recompute_order(order)
    amount_base = gl.to_base(company, order.amount_total, gl.resolve_rate(company, order.currency_id, order.date))
    if p.credit_check:
        exposure = credit_exposure(company, p) + amount_base
        if exposure > p.credit_limit:
            raise BusinessError(f"Credit limit exceeded for {p.name}: exposure {exposure} > limit {p.credit_limit}.",
                                "credit_limit", {"exposure": str(exposure), "limit": str(p.credit_limit)})
    if not ensure_approved(company, DocumentType.SALES_ORDER, order, amount_base, user):
        return order
    order.number = Sequence.next_number(company, "doc:SO", order.date.year, "SO")
    order.state = OrderState.CONFIRMED
    order.confirmed_by = user
    order.confirmed_at = timezone.now()
    order.save()
    audit.record("confirm", order, {"number": order.number, "total": order.amount_total})
    emit(company, "sales_order.confirmed", {"id": order.pk, "number": order.number, "partner": p.code,
                                            "total": order.amount_total, "currency": order.currency_id})
    return order


@transaction.atomic
def deliver(order, user, quantities=None, on_date=None):
    """quantities: {line_id: qty}; default = everything outstanding."""
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.state != OrderState.CONFIRMED:
        raise BusinessError("Only confirmed orders can be delivered.", "invalid_state")
    on_date = on_date or timezone.localdate()
    moves = []
    for ln in order.lines.select_for_update().select_related("product", "product__category"):
        outstanding = ln.quantity - ln.qty_delivered
        qty = D(quantities.get(ln.pk, quantities.get(str(ln.pk), 0))) if quantities is not None else outstanding
        if qty <= 0:
            continue
        if qty > outstanding:
            raise BusinessError(f"Line {ln.product.sku}: delivering {qty} but only {outstanding} outstanding.",
                                "over_delivery")
        if ln.product.is_stock:
            cogs = gl.require_account(ln.product.category.cogs_account, f"COGS (category {ln.product.category})")
            moves.append(stock.issue(order.company, ln.product, order.warehouse, qty, on_date, user,
                                     counter_account=cogs, move_type=MoveType.DELIVERY, partner=order.partner,
                                     reference=order.number, sales_line=ln))
        ln.qty_delivered += qty
        ln.save(update_fields=["qty_delivered"])
    if not moves and quantities is not None and not any(D(v) > 0 for v in quantities.values()):
        raise BusinessError("Nothing to deliver.", "nothing_to_deliver")
    _maybe_done(order)
    return moves


@transaction.atomic
def create_invoice(order, user, on_date=None):
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.state != OrderState.CONFIRMED:
        raise BusinessError("Only confirmed orders can be invoiced.", "invalid_state")
    pending = Invoice.objects.filter(sales_order=order, state__in=[DocState.DRAFT, DocState.TO_APPROVE,
                                                                   DocState.APPROVED]).first()
    if pending:
        raise BusinessError(f"Draft invoice {pending.pk} already exists for this order. Post or cancel it first.",
                            "draft_exists")
    inv = Invoice.objects.create(company=order.company, move_type=InvoiceType.OUT_INVOICE, partner=order.partner,
                                 date=on_date or timezone.localdate(), currency=order.currency,
                                 payment_term=order.payment_term or order.partner.payment_term,
                                 reference=order.customer_reference or order.number, sales_order=order,
                                 created_by=user)
    n = 0
    for ln in order.lines.select_related("product", "product__category"):
        basis = ln.qty_delivered
        qty = basis - ln.qty_invoiced
        if qty <= 0:
            continue
        acc = gl.require_account(ln.product.account("income"), f"income for {ln.product.sku}")
        InvoiceLine.objects.create(invoice=inv, sequence=n, product=ln.product,
                                   description=ln.description or ln.product.name, account=acc, quantity=qty,
                                   unit_price=ln.unit_price, discount_pct=ln.discount_pct, tax=ln.tax, sales_line=ln,
                                   withholding_tax=order.partner.withholding_tax)
        n += 1
    if not n:
        raise BusinessError("Nothing to invoice: deliver goods/services first.", "nothing_to_invoice")
    recompute(inv)
    audit.record("create_invoice", order, {"invoice": inv.pk})
    return inv


def _maybe_done(order):
    lines = order.lines.all()
    if all(ln.qty_delivered >= ln.quantity and ln.qty_invoiced >= ln.quantity for ln in lines):
        order.state = OrderState.DONE
        order.save(update_fields=["state", "updated_at"])


@transaction.atomic
def cancel(order, user, reason=""):
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.state in (OrderState.DONE, OrderState.CANCELLED):
        raise BusinessError(f"Order is {order.state}.", "invalid_state")
    if order.lines.filter(qty_delivered__gt=0).exists() or order.lines.filter(qty_invoiced__gt=0).exists():
        raise BusinessError("Goods were delivered or invoiced; return them / issue a credit note instead.",
                            "has_activity")
    order.state = OrderState.CANCELLED
    order.save(update_fields=["state", "updated_at"])
    audit.record("cancel", order, {"reason": reason})
    return order
