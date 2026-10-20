"""Manufacturing with actual absorption costing.

  Consume components     Dr Work in Progress            / Cr Raw-material inventory  (at FIFO/average cost)
  Record operations      Dr Work in Progress            / Cr Labour absorbed, Overhead absorbed (hours × rates)
  Produce                Dr Finished-goods inventory    / Cr Work in Progress        (actual unit cost)
  Scrap                  Dr Production scrap & variances / Cr Work in Progress
Actual factory wages and overhead are booked as usual to the "actual" accounts; the net of actual and
absorbed accounts is the under/over-absorption for the period.
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core import audit
from apps.core.exceptions import BusinessError
from apps.core.models import Sequence
from apps.core.money import ZERO, D, round_to
from apps.inventory import services as stock
from apps.inventory.models import MoveType
from apps.ledger import services as gl

from .models import ManufacturingOrder, MoComponent, MoOperation, MoState


def _accounts(company):
    st = gl.settings_for(company)
    return (gl.require_account(st.wip_account, "work in progress"),
            gl.require_account(st.labour_absorbed_account, "labour absorbed"),
            gl.require_account(st.overhead_absorbed_account, "overhead absorbed"),
            gl.require_account(st.production_scrap_account, "production scrap"))


def planned_cost(bom, qty):
    """Standard-style estimate from current average costs and work-center rates."""
    factor = D(qty) / bom.quantity
    material = sum((c.quantity * factor * (1 + c.scrap_pct / 100) * c.product.valuation.average_cost
                    if hasattr(c.product, "valuation") else ZERO for c in bom.components.select_related("product")),
                   ZERO)
    conv = sum((op.minutes * factor / 60 * (op.work_center.labour_rate + op.work_center.overhead_rate)
                for op in bom.operations.select_related("work_center")), ZERO)
    return {"material": round_to(material, 2), "conversion": round_to(conv, 2), "total": round_to(material + conv, 2)}


@transaction.atomic
def confirm(mo, user):
    mo = ManufacturingOrder.objects.select_for_update().select_related("bom", "product").get(pk=mo.pk)
    if mo.state != MoState.DRAFT:
        raise BusinessError("Only draft orders can be confirmed.", "invalid_state")
    if mo.bom.product_id != mo.product_id or not mo.bom.active:
        raise BusinessError("Choose an active bill of materials for this product.", "invalid_bom")
    if not mo.product.is_stock:
        raise BusinessError("The finished product must be a stockable product.", "not_stockable")
    _accounts(mo.company)
    factor = mo.quantity / mo.bom.quantity
    for c in mo.bom.components.all():
        MoComponent.objects.create(mo=mo, product=c.product,
                                   planned_qty=round_to(c.quantity * factor * (1 + c.scrap_pct / 100), 4))
    for op in mo.bom.operations.all():
        MoOperation.objects.create(mo=mo, sequence=op.sequence, name=op.name, work_center=op.work_center,
                                   planned_minutes=round_to(op.minutes * factor, 2))
    mo.number = Sequence.next_number(mo.company, "doc:MO", mo.planned_date.year, "MO")
    mo.state = MoState.CONFIRMED
    mo.save()
    audit.record("confirm", mo, {"product": mo.product.sku, "qty": mo.quantity})
    return mo


def _open(mo):
    if mo.state not in (MoState.CONFIRMED, MoState.IN_PROGRESS):
        raise BusinessError("The manufacturing order is not open.", "invalid_state")


@transaction.atomic
def consume(mo, user, quantities=None, on_date=None):
    """Issue components to WIP. quantities: {component_line_id: qty}; default = remaining planned."""
    mo = ManufacturingOrder.objects.select_for_update().get(pk=mo.pk)
    _open(mo)
    wip, *_ = _accounts(mo.company)
    on_date = on_date or timezone.localdate()
    moves = []
    for c in mo.components.select_for_update().select_related("product", "product__category"):
        qty = D(quantities.get(c.pk, quantities.get(str(c.pk), 0))) if quantities is not None else \
            max(ZERO, c.planned_qty - c.consumed_qty)
        if qty <= 0:
            continue
        m = stock.issue(mo.company, c.product, mo.warehouse, qty, on_date, user, counter_account=wip,
                        move_type=MoveType.PRODUCTION_CONSUME, reference=mo.number, manufacturing_order=mo)
        c.consumed_qty += qty
        c.consumed_value += m.value
        c.save()
        mo.material_cost += m.value
        moves.append(m)
    if not moves:
        raise BusinessError("Nothing to consume.", "nothing_to_consume")
    mo.state = MoState.IN_PROGRESS
    mo.save()
    return moves


@transaction.atomic
def record_operations(mo, user, minutes, on_date=None):
    """Absorb conversion cost into WIP. minutes: {operation_line_id: actual minutes to add}."""
    mo = ManufacturingOrder.objects.select_for_update().get(pk=mo.pk)
    _open(mo)
    wip, lab_acc, ovh_acc, _ = _accounts(mo.company)
    on_date = on_date or timezone.localdate()
    decimals = gl.base_decimals(mo.company)
    labour = overhead = ZERO
    for op in mo.operations.select_for_update().select_related("work_center"):
        m = D(minutes.get(op.pk, minutes.get(str(op.pk), 0)))
        if m <= 0:
            continue
        lab = round_to(m / 60 * op.work_center.labour_rate, decimals)
        ovh = round_to(m / 60 * op.work_center.overhead_rate, decimals)
        op.actual_minutes += m
        op.labour_cost += lab
        op.overhead_cost += ovh
        op.save()
        labour += lab
        overhead += ovh
    if not labour and not overhead:
        raise BusinessError("Enter the minutes worked (and set work-center rates).", "nothing_to_record")
    specs = [gl.LineSpec(account=wip, debit=labour + overhead, description=f"Conversion cost {mo.number}")]
    if labour:
        specs.append(gl.LineSpec(account=lab_acc, credit=labour, description=f"Labour absorbed {mo.number}"))
    if overhead:
        specs.append(gl.LineSpec(account=ovh_acc, credit=overhead, description=f"Overhead absorbed {mo.number}"))
    e = gl.create_entry(mo.company, gl.journal_for(mo.company, "inventory_journal", "inventory"), on_date, specs,
                        user=user, reference=mo.number, narration=f"Labour and overhead absorbed — {mo.number}",
                        source=("manufacturing_order", mo.pk))
    gl.post_entry(e, user, allow_control=True)
    mo.labour_cost += labour
    mo.overhead_cost += overhead
    mo.state = MoState.IN_PROGRESS
    mo.save()
    return e


@transaction.atomic
def produce(mo, user, qty, scrap_qty=0, on_date=None, finish=False):
    """Receive finished goods at actual cost. While the order stays open, output is valued at the
    average WIP per unit still to be produced; `finish` closes the order and clears all remaining WIP
    (into the goods produced now, scrap at the same unit cost)."""
    mo = ManufacturingOrder.objects.select_for_update().select_related("product").get(pk=mo.pk)
    _open(mo)
    wip, _, _, scrap_acc = _accounts(mo.company)
    qty, scrap_qty = D(qty), D(scrap_qty)
    on_date = on_date or timezone.localdate()
    if qty < 0 or scrap_qty < 0 or (qty + scrap_qty) <= 0:
        raise BusinessError("Enter a produced and/or scrapped quantity.", "invalid_qty")
    remaining_units = max(mo.quantity - mo.produced_qty - mo.scrapped_qty, qty + scrap_qty)
    balance = mo.wip_balance
    if balance <= 0:
        raise BusinessError("No cost in WIP yet: consume components / record operations first.", "empty_wip")
    decimals = gl.base_decimals(mo.company)
    units = qty + scrap_qty
    portion = balance if finish else round_to(balance * units / remaining_units, decimals)
    good_value = round_to(portion * qty / units, decimals) if units else ZERO
    scrap_value = portion - good_value
    move = None
    if qty:
        move = stock.receive(mo.company, mo.product, mo.warehouse, qty, good_value / qty, on_date, user,
                             counter_account=wip, move_type=MoveType.PRODUCTION_OUTPUT, reference=mo.number,
                             manufacturing_order=mo)
        good_value = move.value  # after rounding of unit cost × qty
        scrap_value = portion - good_value
    if scrap_value:
        e = gl.create_entry(mo.company, gl.journal_for(mo.company, "inventory_journal", "inventory"), on_date, [
            gl.LineSpec(account=scrap_acc, debit=scrap_value, description=f"Scrap {mo.number}"),
            gl.LineSpec(account=wip, credit=scrap_value, description=f"Scrap {mo.number}"),
        ], user=user, reference=mo.number, narration=f"Production scrap — {mo.number}",
            source=("manufacturing_order", mo.pk))
        gl.post_entry(e, user, allow_control=True)
    mo.produced_qty += qty
    mo.scrapped_qty += scrap_qty
    mo.output_value += good_value
    mo.scrap_value += scrap_value
    if finish:
        mo.state, mo.date_done = MoState.DONE, on_date
    mo.save()
    audit.record("produce", mo, {"qty": qty, "scrap": scrap_qty, "value": good_value, "finish": finish})
    return mo


@transaction.atomic
def cancel(mo, user):
    mo = ManufacturingOrder.objects.select_for_update().get(pk=mo.pk)
    if mo.state in (MoState.DONE, MoState.CANCELLED):
        raise BusinessError("Order is already closed.", "invalid_state")
    if mo.material_cost or mo.labour_cost or mo.overhead_cost:
        raise BusinessError("Costs were already booked; finish the order (scrap the remainder) instead.",
                            "has_costs")
    mo.state = MoState.CANCELLED
    mo.save(update_fields=["state", "updated_at"])
    return mo


def cost_report(mo):
    plan = planned_cost(mo.bom, mo.quantity)
    actual = mo.material_cost + mo.labour_cost + mo.overhead_cost
    unit = (mo.output_value / mo.produced_qty) if mo.produced_qty else None
    return {"planned": plan, "actual": {"material": str(mo.material_cost), "labour": str(mo.labour_cost),
                                        "overhead": str(mo.overhead_cost), "total": str(actual)},
            "variance": str(round_to(actual - plan["total"] * (mo.produced_qty + mo.scrapped_qty) / mo.quantity, 2))
            if mo.state == MoState.DONE else None,
            "output_value": str(mo.output_value), "scrap_value": str(mo.scrap_value),
            "wip_balance": str(mo.wip_balance), "actual_unit_cost": str(round_to(unit, 4)) if unit else None}


def wip_report(company):
    from django.db.models import Sum
    from apps.ledger.models import JournalLine
    open_mos = ManufacturingOrder.objects.filter(company=company, state__in=[MoState.CONFIRMED, MoState.IN_PROGRESS])
    rows = [{"id": m.pk, "number": m.number, "product": str(m.product), "quantity": str(m.quantity),
             "produced": str(m.produced_qty), "wip": str(m.wip_balance)} for m in open_mos]
    total = sum((m.wip_balance for m in open_mos), ZERO)
    agg = JournalLine.objects.filter(company=company, posted=True, account__account_type="wip").aggregate(
        d=Sum("debit"), c=Sum("credit"))
    gl_wip = (agg["d"] or ZERO) - (agg["c"] or ZERO)
    return {"rows": rows, "total": str(total), "general_ledger": str(gl_wip), "reconciled": total == gl_wip}
