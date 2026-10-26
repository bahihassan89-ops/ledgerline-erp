"""Manufacturing with actual absorption costing.

  Consume components     Dr Work in Progress            / Cr Raw-material inventory  (at FIFO/average cost)
  Record operations      Dr Work in Progress            / Cr Labour absorbed, Overhead absorbed (hours × rates)
  Produce                Dr Finished-goods inventory    / Cr Work in Progress        (actual unit cost)
  Scrap                  Dr Production scrap & variances / Cr Work in Progress
Actual factory wages and overhead are booked as usual to the "actual" accounts; the net of actual and
absorbed accounts is the under/over-absorption for the period.
"""
from datetime import timedelta
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

from .models import (Bom, BomByproduct, BomComponent, BomOperation, ManufacturingOrder, MoByproduct, MoComponent,
                     MoOperation, MoState, QualityInspection)

MAX_SCHEDULING_HORIZON_DAYS = 365


def _accounts(company):
    st = gl.settings_for(company)
    return (gl.require_account(st.wip_account, "work in progress"),
            gl.require_account(st.labour_absorbed_account, "labour absorbed"),
            gl.require_account(st.overhead_absorbed_account, "overhead absorbed"),
            gl.require_account(st.production_scrap_account, "production scrap"))


def _capacity_on(work_center, day):
    """Minutes `work_center` can absorb on `day`: a calendar exception overrides everything else
    (holiday = 0, extra shift = a bigger number); otherwise its normal daily capacity applies on
    working weekdays and zero on non-working ones."""
    exc = work_center.calendar_exceptions.filter(date=day).first()
    if exc is not None:
        return exc.available_minutes
    if day.weekday() not in work_center.working_weekday_set:
        return ZERO
    return work_center.capacity_minutes_per_day


def _op_minutes_on_day(op, day):
    """How many minutes of an already-scheduled operation fall on `day`, assuming (consistently with
    how schedule_operations lays work out) that it consumes full daily capacity on every day of its
    span except the last, which takes whatever remainder is left."""
    if day == op.scheduled_end:
        used, d = ZERO, op.scheduled_start
        while d < op.scheduled_end:
            used += _capacity_on(op.work_center, d)
            d += timedelta(days=1)
        return op.planned_minutes - used
    return _capacity_on(op.work_center, day)


def _work_center_load_on(work_center, day):
    """Minutes other orders' operations have already claimed at this work center on `day`."""
    qs = MoOperation.objects.filter(work_center=work_center, scheduled_start__isnull=False,
                                    scheduled_start__lte=day, scheduled_end__gte=day)
    return sum((_op_minutes_on_day(op, day) for op in qs), ZERO)


def _pick_work_center(primary, alternates, day):
    """The primary work center if it has any free capacity on `day`; otherwise the first alternate
    (in order) that does; falls back to the primary if none have room (it will simply run over into
    following days, same as if it had been chosen directly)."""
    for wc in [primary, *alternates]:
        if _capacity_on(wc, day) - _work_center_load_on(wc, day) > 0:
            return wc
    return primary


def schedule_operations(mo):
    """Greedy finite-capacity, day-granularity scheduler. Walks the MO's operations in sequence: each
    starts no earlier than the previous operation's end, picks the first work center (primary, then
    its alternates) with free capacity on that start day, then consumes free daily capacity of that
    work center (net of what other orders have already claimed) day by day until planned_minutes is
    exhausted. Safe to call again (e.g. from the `reschedule` action) -- it always starts from a clean
    slate for this MO's own operations."""
    mo.operations.update(scheduled_start=None, scheduled_end=None)
    cursor = mo.planned_date
    for op in (mo.operations.select_related("work_center").prefetch_related("alternate_work_centers")
              .order_by("sequence", "id")):
        alternates = list(op.alternate_work_centers.all())
        chosen = _pick_work_center(op.work_center, alternates, cursor)
        if op.planned_minutes <= 0:
            op.work_center, op.scheduled_start, op.scheduled_end = chosen, cursor, cursor
            op.save(update_fields=["work_center", "scheduled_start", "scheduled_end"])
            cursor = op.scheduled_end
            continue
        remaining, day, horizon = op.planned_minutes, cursor, 0
        while True:
            horizon += 1
            if horizon > MAX_SCHEDULING_HORIZON_DAYS:
                raise BusinessError(f"Could not schedule operation {op.name!r}: no free capacity found at "
                                    f"{chosen} (or its alternates) within {MAX_SCHEDULING_HORIZON_DAYS} days.",
                                    "scheduling_horizon_exceeded")
            free = _capacity_on(chosen, day) - _work_center_load_on(chosen, day)
            if free > 0:
                remaining -= min(remaining, free)
            if remaining <= 0:
                break
            day += timedelta(days=1)
        op.work_center, op.scheduled_start, op.scheduled_end = chosen, cursor, day
        op.save(update_fields=["work_center", "scheduled_start", "scheduled_end"])
        cursor = day
    return mo


@transaction.atomic
def reschedule(mo):
    """Re-run the finite-capacity scheduler for an open order -- e.g. after a BOM operation's work
    center changed, or another order's schedule freed up/claimed capacity this one should react to."""
    mo = ManufacturingOrder.objects.select_for_update().get(pk=mo.pk)
    _open(mo)
    return schedule_operations(mo)


def planned_cost(bom, qty, _seen=None):
    """Standard-style estimate from current average costs and work-center rates. Recurses through
    sub-assembly levels: if a component is itself a manufactured product with its own active BOM, its
    planned cost (material + conversion) is rolled up instead of using its (likely stale/zero) average
    cost -- this is what makes multi-level BOMs cost correctly."""
    _seen = _seen or set()
    if bom.pk in _seen:
        return {"material": ZERO, "conversion": ZERO, "total": ZERO}  # defensive: cycle guard
    _seen = _seen | {bom.pk}
    factor = D(qty) / bom.quantity
    material = ZERO
    for c in bom.components.select_related("product", "product__valuation"):
        c_qty = c.quantity * factor * (1 + c.scrap_pct / 100)
        sub_bom = c.product.boms.filter(active=True).first()
        if sub_bom is not None:
            material += planned_cost(sub_bom, c_qty, _seen)["total"]
        elif hasattr(c.product, "valuation"):
            material += c_qty * c.product.valuation.average_cost
    conv = sum((op.minutes * factor / 60 * (op.work_center.labour_rate + op.work_center.overhead_rate)
                for op in bom.operations.select_related("work_center")), ZERO)
    return {"material": round_to(material, 2), "conversion": round_to(conv, 2), "total": round_to(material + conv, 2)}


@transaction.atomic
def revise_bom(bom, user):
    """Create the next version of a BOM: copies code/product/quantity/notes/components/operations onto
    a new row, marks the old version inactive, and the new version active. Unlike editing a BOM in
    place, this is always allowed (even while open MOs reference the old version), because it never
    touches the version those orders already locked in -- it only affects *new* orders created after
    this point. This is the "create a new BOM version instead" path that BomSerializer.update() points
    to when it refuses an in-place edit of a BOM already used by an open order."""
    bom = Bom.objects.select_for_update().prefetch_related(
        "components", "operations__alternate_work_centers", "byproducts").get(pk=bom.pk)
    if not bom.active:
        raise BusinessError("Only the active version of a BOM can be revised.", "not_active")
    components = list(bom.components.all())
    operations = list(bom.operations.all())
    byproducts = list(bom.byproducts.all())
    # Deactivate the old version first: "one active version per code" is enforced by a partial unique
    # constraint, so the new (active) row can only be inserted once the old one has stepped aside.
    bom.active = False
    bom.save(update_fields=["active", "updated_at"])
    new = Bom.objects.create(company=bom.company, code=bom.code, version=bom.version + 1, revises=bom,
                             product=bom.product, quantity=bom.quantity, active=True, backflush=bom.backflush,
                             notes=bom.notes, created_by=user)
    for c in components:
        BomComponent.objects.create(bom=new, product=c.product, quantity=c.quantity, scrap_pct=c.scrap_pct)
    for op in operations:
        new_op = BomOperation.objects.create(bom=new, sequence=op.sequence, name=op.name,
                                             work_center=op.work_center, minutes=op.minutes)
        alt_ids = list(op.alternate_work_centers.values_list("pk", flat=True))
        if alt_ids:
            new_op.alternate_work_centers.set(alt_ids)
    for bp in byproducts:
        BomByproduct.objects.create(bom=new, product=bp.product, quantity=bp.quantity, unit_value=bp.unit_value)
    audit.record("revise", new, {"code": new.code, "from_version": bom.version, "to_version": new.version})
    return new


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
    for op in mo.bom.operations.all().prefetch_related("alternate_work_centers"):
        mo_op = MoOperation.objects.create(mo=mo, sequence=op.sequence, name=op.name, work_center=op.work_center,
                                           planned_minutes=round_to(op.minutes * factor, 2))
        alt_ids = list(op.alternate_work_centers.values_list("pk", flat=True))
        if alt_ids:
            mo_op.alternate_work_centers.set(alt_ids)
    for bp in mo.bom.byproducts.all():
        MoByproduct.objects.create(mo=mo, product=bp.product, planned_qty=round_to(bp.quantity * factor, 4),
                                   unit_value=bp.unit_value)
    mo.number = Sequence.next_number(mo.company, "doc:MO", mo.planned_date.year, "MO")
    mo.state = MoState.CONFIRMED
    mo.save()
    schedule_operations(mo)
    audit.record("confirm", mo, {"product": mo.product.sku, "qty": mo.quantity})
    return mo


def _open(mo):
    if mo.state not in (MoState.CONFIRMED, MoState.IN_PROGRESS):
        raise BusinessError("The manufacturing order is not open.", "invalid_state")


@transaction.atomic
def consume(mo, user, quantities=None, on_date=None, lots=None, serials=None):
    """Issue components to WIP. quantities: {component_line_id: qty}; default = remaining planned.
    lots: {component_line_id: lot_number}, for lot-tracked components -- restricts FIFO consumption
    to that lot (see inventory.services.issue) so the raw-material batch is traceable into this MO.
    serials: {component_line_id: [serial_number, ...]}, required (one per unit) for serial-tracked
    components."""
    mo = ManufacturingOrder.objects.select_for_update().get(pk=mo.pk)
    _open(mo)
    wip, *_ = _accounts(mo.company)
    on_date = on_date or timezone.localdate()
    lots = lots or {}
    serials = serials or {}
    moves = []
    for c in mo.components.select_for_update().select_related("product", "product__category"):
        qty = D(quantities.get(c.pk, quantities.get(str(c.pk), 0))) if quantities is not None else \
            max(ZERO, c.planned_qty - c.consumed_qty)
        if qty <= 0:
            continue
        lot_number = lots.get(c.pk) or lots.get(str(c.pk))
        serial_list = serials.get(c.pk) or serials.get(str(c.pk))
        m = stock.issue(mo.company, c.product, mo.warehouse, qty, on_date, user, counter_account=wip,
                        move_type=MoveType.PRODUCTION_CONSUME, reference=mo.number, manufacturing_order=mo,
                        lot_number=lot_number, serials=serial_list)
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
def record_subcontract_cost(mo, user, op_id, amount, on_date=None):
    """Book a vendor's invoiced charge for an operation performed at a subcontracted work center,
    straight into WIP (Dr Work in Progress / Cr Subcontracting clearing) -- the vendor bill itself
    clears that account when it's entered through purchasing, exactly like GRNI clearing."""
    mo = ManufacturingOrder.objects.select_for_update().get(pk=mo.pk)
    _open(mo)
    op = mo.operations.select_related("work_center", "work_center__default_vendor").get(pk=op_id)
    if not op.work_center.is_subcontracted:
        raise BusinessError(f"{op.work_center} is not a subcontracted work center.", "not_subcontracted")
    amount = D(amount)
    if amount <= 0:
        raise BusinessError("Enter the vendor's charge.", "invalid_amount")
    on_date = on_date or timezone.localdate()
    st = gl.settings_for(mo.company)
    wip = gl.require_account(st.wip_account, "work in progress")
    clearing = gl.require_account(st.subcontracting_clearing_account, "subcontracting clearing")
    e = gl.create_entry(mo.company, gl.journal_for(mo.company, "inventory_journal", "inventory"), on_date, [
        gl.LineSpec(account=wip, debit=amount, description=f"Subcontract {op.name} {mo.number}"),
        gl.LineSpec(account=clearing, credit=amount, partner=op.work_center.default_vendor,
                    description=f"Subcontract {op.name} {mo.number}"),
    ], user=user, reference=mo.number, narration=f"Subcontract charge — {op.name} ({mo.number})",
        source=("manufacturing_order", mo.pk))
    gl.post_entry(e, user, allow_control=True)
    op.subcontract_cost += amount
    op.save(update_fields=["subcontract_cost"])
    mo.overhead_cost += amount  # rolled into conversion cost so wip_balance / cost reports stay correct
    mo.state = MoState.IN_PROGRESS
    mo.save()
    audit.record("subcontract", mo, {"operation": op.name, "amount": amount})
    return e


@transaction.atomic
def create_subcontract_bill(mo, user, op_id, amount, on_date=None, vendor_reference="", tax=None):
    """Draft a vendor bill for a subcontracted operation's charge, referencing the operation
    (InvoiceLine.mo_operation) so it can never bill more than record_subcontract_cost actually
    charged to WIP. Posting the bill (apps.billing.services.post_invoice) debits Subcontracting
    Clearing and credits Accounts Payable, exactly how a stock bill clears GRNI."""
    from apps.billing.models import Invoice, InvoiceLine, InvoiceType
    from apps.billing.services import recompute

    mo = ManufacturingOrder.objects.select_for_update().select_related("company").get(pk=mo.pk)
    op = mo.operations.select_related("work_center", "work_center__default_vendor").get(pk=op_id)
    if not op.work_center.is_subcontracted:
        raise BusinessError(f"{op.work_center} is not a subcontracted work center.", "not_subcontracted")
    vendor = op.work_center.default_vendor
    if not vendor:
        raise BusinessError(f"{op.work_center} has no default vendor.", "no_vendor")
    amount = D(amount)
    outstanding = op.subcontract_cost - op.subcontract_billed
    if amount <= 0 or amount > outstanding:
        raise BusinessError(f"Can bill at most {outstanding} (already charged to WIP, not yet billed).",
                            "over_bill")
    st = gl.settings_for(mo.company)
    acc = gl.require_account(st.subcontracting_clearing_account, "subcontracting clearing")
    bill = Invoice.objects.create(company=mo.company, move_type=InvoiceType.IN_INVOICE, partner=vendor,
                                  date=on_date or timezone.localdate(), currency=mo.company.base_currency,
                                  payment_term=vendor.payment_term, reference=vendor_reference,
                                  created_by=user)
    InvoiceLine.objects.create(invoice=bill, sequence=0, description=f"Subcontract {op.name} — {mo.number}",
                               account=acc, quantity=Decimal("1"), unit_price=amount, tax=tax,
                               mo_operation=op, withholding_tax=vendor.withholding_tax)
    recompute(bill)
    op.subcontract_billed += amount
    op.save(update_fields=["subcontract_billed"])
    audit.record("create_subcontract_bill", mo, {"operation": op.name, "bill": bill.pk, "amount": amount})
    return bill


@transaction.atomic
def inspect(mo, user, qty_passed, qty_failed=0, notes="", on_date=None):
    """Record a quality-control inspection of finished output for a `requires_qc` product. Passed
    quantity becomes available for produce() to release (see _consume_quality_pass); failed quantity
    is simply recorded for traceability -- it never becomes available output."""
    mo = ManufacturingOrder.objects.select_for_update().select_related("product").get(pk=mo.pk)
    _open(mo)
    if not mo.product.requires_qc:
        raise BusinessError(f"{mo.product} does not require quality inspection.", "qc_not_required")
    qty_passed, qty_failed = D(qty_passed), D(qty_failed)
    if qty_passed < 0 or qty_failed < 0 or (qty_passed + qty_failed) <= 0:
        raise BusinessError("Enter a passed and/or failed quantity.", "invalid_qty")
    qi = QualityInspection.objects.create(company=mo.company, mo=mo, qty_passed=qty_passed, qty_failed=qty_failed,
                                          notes=notes, inspected_at=on_date or timezone.localdate())
    audit.record("inspect", qi, {"mo": mo.number, "passed": str(qty_passed), "failed": str(qty_failed)})
    return qi


def _consume_quality_pass(mo, qty):
    """Draw down `qty` of QC-passed-but-not-yet-released output, oldest inspection first. This is the
    quality gate itself: produce() cannot release more than has actually passed inspection."""
    qty = D(qty)
    inspections = list(mo.quality_inspections.select_for_update().order_by("inspected_at", "id"))
    available = sum((i.qty_passed - i.qty_used for i in inspections), ZERO)
    if qty > available:
        raise BusinessError(f"Only {available} unit(s) of {mo.number} have passed quality inspection "
                            f"(requested {qty}); inspect more output before producing it.", "qc_not_passed")
    remaining = qty
    for i in inspections:
        if remaining <= 0:
            break
        free = i.qty_passed - i.qty_used
        if free <= 0:
            continue
        take = min(remaining, free)
        i.qty_used += take
        i.save(update_fields=["qty_used"])
        remaining -= take


def _backflush(mo, user, qty, scrap_qty, on_date):
    """Auto-consume each component's BOM-implied shortfall for the cumulative output produced so far
    (this call included), instead of requiring an explicit consume() first. Lot-tracked components
    still raise `lot_required` inside stock.issue() if not already (manually) consumed -- backflush
    only covers components that don't need a human to pick a specific batch/serial."""
    wip, *_ = _accounts(mo.company)
    cumulative = mo.produced_qty + mo.scrapped_qty + qty + scrap_qty
    ratio = cumulative / mo.quantity
    for c in mo.components.select_for_update().select_related("product", "product__category"):
        need = round_to(c.planned_qty * ratio, 4)
        shortfall = need - c.consumed_qty
        if shortfall <= 0:
            continue
        m = stock.issue(mo.company, c.product, mo.warehouse, shortfall, on_date, user, counter_account=wip,
                        move_type=MoveType.PRODUCTION_CONSUME, reference=mo.number, manufacturing_order=mo)
        c.consumed_qty += shortfall
        c.consumed_value += m.value
        c.save()
        mo.material_cost += m.value


def _produce_byproducts(mo, user, byproducts, on_date):
    """Receive any by-products recovered alongside this production run, at their standard recovery
    value, crediting WIP -- this must happen *before* the main product's good/scrap split is costed,
    so the credit correctly reduces the WIP absorbed into the main product."""
    if not byproducts:
        return
    wip, *_ = _accounts(mo.company)
    decimals = gl.base_decimals(mo.company)
    for bp in mo.byproducts.select_for_update().select_related("product"):
        qty = D(byproducts.get(bp.pk, byproducts.get(str(bp.pk), 0)))
        if qty <= 0:
            continue
        stock.receive(mo.company, bp.product, mo.warehouse, qty, bp.unit_value, on_date, user, counter_account=wip,
                     move_type=MoveType.PRODUCTION_OUTPUT, reference=mo.number, manufacturing_order=mo)
        value = round_to(qty * bp.unit_value, decimals)
        bp.produced_qty += qty
        bp.value += value
        bp.save(update_fields=["produced_qty", "value"])
        mo.byproduct_value += value


@transaction.atomic
def produce(mo, user, qty, scrap_qty=0, on_date=None, finish=False, lot_number=None, serials=None,
           byproducts=None):
    """Receive finished goods at actual cost. While the order stays open, output is valued at the
    average WIP per unit still to be produced; `finish` closes the order and clears all remaining WIP
    (into the goods produced now, scrap at the same unit cost). `lot_number` tags the produced batch
    (required if the finished product is lot-tracked), the other end of the genealogy chain started
    by the lots consumed in `consume()` -- see inventory.services.lot_genealogy(). `serials` (one per
    unit) is required if the finished product is serial-tracked. For a `requires_qc` product, `qty`
    cannot exceed what has already passed quality inspection (see inspect()). For a `backflush` BOM,
    components are auto-consumed here instead of needing an explicit consume() call. `byproducts`
    ({byproduct_line_id: qty}) receives any co-products recovered alongside this run."""
    mo = ManufacturingOrder.objects.select_for_update().select_related("product", "bom").get(pk=mo.pk)
    _open(mo)
    wip, _, _, scrap_acc = _accounts(mo.company)
    qty, scrap_qty = D(qty), D(scrap_qty)
    on_date = on_date or timezone.localdate()
    if qty < 0 or scrap_qty < 0 or (qty + scrap_qty) <= 0:
        raise BusinessError("Enter a produced and/or scrapped quantity.", "invalid_qty")
    if qty and mo.product.requires_qc:
        _consume_quality_pass(mo, qty)
    if mo.bom.backflush:
        _backflush(mo, user, qty, scrap_qty, on_date)
    _produce_byproducts(mo, user, byproducts, on_date)
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
                             manufacturing_order=mo, lot_number=lot_number, serials=serials)
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
