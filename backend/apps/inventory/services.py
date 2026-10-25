"""Perpetual inventory with FIFO / weighted-average costing.

Each inbound move creates a cost layer and posts   Dr Inventory / Cr <counter account>.
Each outbound move is costed (FIFO layers or average) and posts   Dr <counter account> / Cr Inventory.
The valuation row of the product is locked (SELECT ... FOR UPDATE) for the whole move, so concurrent
moves on the same product are serialized and costs stay correct.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import F

from apps.core import audit
from apps.core.exceptions import BusinessError
from apps.core.models import Sequence
from apps.core.money import ZERO, D, round_to
from apps.ledger import services as gl

from .models import (INBOUND, CostLayer, CostMethod, Lot, MoveType, ProductValuation, SerialNumber, SerialStatus,
                     StockMove, StockMoveSerial, StockQuant)


def _lock_valuation(company, product):
    ProductValuation.objects.get_or_create(company=company, product=product)
    return ProductValuation.objects.select_for_update().get(product=product)


def _lock_quant(company, product, warehouse):
    StockQuant.objects.get_or_create(company=company, product=product, warehouse=warehouse)
    return StockQuant.objects.select_for_update().get(product=product, warehouse=warehouse)


def _check(company, product, warehouse, qty):
    if product.company_id != company.pk or warehouse.company_id != company.pk:
        raise BusinessError("Product or warehouse belongs to another company.", "cross_company")
    if not product.is_stock:
        raise BusinessError(f"{product} is not a stockable product.", "not_stockable")
    if D(qty) <= 0:
        raise BusinessError("Quantity must be positive.", "invalid_qty")


def _resolve_lot(company, product, lot, lot_number):
    """Validate/create the Lot for a move. Lot-tracked products must supply one (either an existing
    Lot instance or a lot_number to get-or-create); other products may optionally tag a lot too."""
    if lot is not None:
        if lot.company_id != company.pk or lot.product_id != product.pk:
            raise BusinessError(f"Lot {lot.lot_number} does not belong to {product} in this company.",
                                "invalid_lot")
        return lot
    if lot_number:
        lot, _ = Lot.objects.get_or_create(company=company, product=product, lot_number=lot_number)
        return lot
    if product.track_lots:
        raise BusinessError(f"{product} is lot-tracked: a lot/batch number is required.", "lot_required")
    return None


def _resolve_serials(company, product, warehouse, qty, serials, inbound):
    """Validate/create SerialNumber rows for a move of `qty` units. `serials` is a list of serial
    number strings, one per unit -- required for products with track_serials. Inbound moves create
    (or revalidate, e.g. for a customer return) the serial as `in_stock` at `warehouse`; outbound
    moves require the serial to already exist and be in stock (status is finalised afterwards by
    the caller, once the move type -- consumed / delivered / scrapped -- is known)."""
    serials = serials or []
    if not product.track_serials:
        return []
    if D(len(serials)) != D(qty):
        raise BusinessError(f"{product} is serial-tracked: give exactly {qty} serial number(s) "
                            f"(got {len(serials)}).", "serial_count_mismatch")
    rows = []
    for sn in serials:
        if inbound:
            s, _ = SerialNumber.objects.get_or_create(company=company, product=product, serial_number=sn,
                                                       defaults={"status": SerialStatus.IN_STOCK,
                                                                 "warehouse": warehouse})
            s.status, s.warehouse = SerialStatus.IN_STOCK, warehouse
            s.save(update_fields=["status", "warehouse"])
        else:
            s = SerialNumber.objects.filter(company=company, product=product, serial_number=sn).first()
            if s is None:
                raise BusinessError(f"Unknown serial number {sn} for {product}.", "unknown_serial")
            if s.status != SerialStatus.IN_STOCK:
                raise BusinessError(f"Serial {sn} is not in stock (status: {s.get_status_display()}).",
                                    "serial_not_available")
        rows.append(s)
    return rows


def _finalize_outbound_serials(serial_rows, move_type):
    if not serial_rows:
        return
    if move_type == MoveType.PRODUCTION_CONSUME:
        status = SerialStatus.CONSUMED
    elif move_type == MoveType.ADJUST_OUT:
        status = SerialStatus.SCRAPPED
    else:
        status = SerialStatus.DELIVERED
    for s in serial_rows:
        s.status = status
        s.save(update_fields=["status"])


def _inventory_account(product):
    return gl.require_account(product.category.inventory_account, f"inventory (category {product.category})")


def _number(company, on_date, inbound):
    return Sequence.next_number(company, "doc:STK-IN" if inbound else "doc:STK-OUT", on_date.year,
                                "WH/IN" if inbound else "WH/OUT")


def _post(company, move, debit_acc, credit_acc, user, partner=None, description=""):
    if move.value == 0:
        return None
    journal = gl.journal_for(company, "inventory_journal", "inventory")
    desc = description or f"{move.get_move_type_display()} {move.product.sku} × {move.quantity}"
    entry = gl.create_entry(company, journal, move.date, [
        gl.LineSpec(account=debit_acc, debit=move.value, product=move.product, quantity=move.quantity,
                    partner=partner if debit_acc.reconcilable else None, description=desc),
        gl.LineSpec(account=credit_acc, credit=move.value, product=move.product, quantity=move.quantity,
                    partner=partner if credit_acc.reconcilable else None, description=desc),
    ], user=user, reference=move.number + (f" {move.reference}" if move.reference else ""),
        source=("stock_move", move.pk))
    gl.post_entry(entry, user, allow_control=True)
    move.journal_entry = entry
    move.save(update_fields=["journal_entry"])
    return entry


@transaction.atomic
def receive(company, product, warehouse, qty, unit_cost, on_date, user, *, counter_account,
            move_type=MoveType.RECEIPT, partner=None, reference="", purchase_line=None, sales_line=None,
            manufacturing_order=None, lot=None, lot_number=None, serials=None):
    """Bring stock in at `unit_cost` (base currency). Credits `counter_account`. `lot`/`lot_number`
    tags the move (and its cost layer) with a batch -- required if the product is lot-tracked.
    `serials` (one per unit) is required if the product is serial-tracked."""
    _check(company, product, warehouse, qty)
    if move_type not in INBOUND:
        raise BusinessError("Not an inbound move type.", "invalid_move")
    qty, unit_cost = D(qty), D(unit_cost)
    if unit_cost < 0:
        raise BusinessError("Unit cost cannot be negative.", "invalid_cost")
    lot = _resolve_lot(company, product, lot, lot_number)
    serial_rows = _resolve_serials(company, product, warehouse, qty, serials, inbound=True)
    val = _lock_valuation(company, product)
    quant = _lock_quant(company, product, warehouse)
    value = round_to(qty * unit_cost, gl.base_decimals(company))
    move = StockMove.objects.create(
        company=company, number=_number(company, on_date, True), date=on_date, move_type=move_type,
        product=product, warehouse=warehouse, quantity=qty, unit_cost=unit_cost, value=value, partner=partner,
        reference=reference, purchase_line=purchase_line, sales_line=sales_line,
        manufacturing_order=manufacturing_order, lot=lot, created_by=user)
    CostLayer.objects.create(company=company, product=product, move=move, lot=lot, date=on_date, quantity=qty,
                             unit_cost=unit_cost, value=value, remaining_qty=qty, remaining_value=value)
    if serial_rows:
        StockMoveSerial.objects.bulk_create([StockMoveSerial(move=move, serial=s) for s in serial_rows])
    val.quantity += qty
    val.value += value
    val.save()
    quant.quantity += qty
    quant.save()
    _post(company, move, _inventory_account(product), counter_account, user, partner)
    audit.record("stock_in", move, {"product": product.sku, "qty": qty, "value": value})
    _emit(company, move)
    return move


@transaction.atomic
def issue(company, product, warehouse, qty, on_date, user, *, counter_account,
          move_type=MoveType.DELIVERY, partner=None, reference="", sales_line=None, purchase_line=None,
          manufacturing_order=None, lot=None, lot_number=None, serials=None):
    """Take stock out, costed by the product's method. Debits `counter_account` (e.g. COGS). When
    `lot`/`lot_number` is given on a FIFO-costed product, consumption is restricted to that lot's own
    cost layers (so genealogy is exact); average-cost products don't segregate value by lot, so the
    lot is recorded on the move for traceability but doesn't change which value is drawn down.
    `serials` (one per unit) is required if the product is serial-tracked; each must already be in
    stock -- its status is finalised (consumed/delivered/scrapped) according to `move_type`."""
    _check(company, product, warehouse, qty)
    if move_type in INBOUND:
        raise BusinessError("Not an outbound move type.", "invalid_move")
    qty = D(qty)
    lot = _resolve_lot(company, product, lot, lot_number)
    serial_rows = _resolve_serials(company, product, warehouse, qty, serials, inbound=False)
    val = _lock_valuation(company, product)
    quant = _lock_quant(company, product, warehouse)
    if quant.quantity < qty:
        raise BusinessError(f"Insufficient stock of {product.sku} in {warehouse.code}: "
                            f"on hand {quant.quantity}, requested {qty}.", "insufficient_stock")
    decimals = gl.base_decimals(company)
    if product.category.cost_method == CostMethod.FIFO:
        value, need = ZERO, qty
        layers = CostLayer.objects.select_for_update().filter(product=product, remaining_qty__gt=0)
        if lot is not None:
            layers = layers.filter(lot=lot)
        for layer in layers.order_by("date", "id"):
            take = min(need, layer.remaining_qty)
            if take == layer.remaining_qty:
                part = layer.remaining_value
            else:
                part = round_to(take * layer.remaining_value / layer.remaining_qty, decimals)
            layer.remaining_qty -= take
            layer.remaining_value -= part
            layer.save(update_fields=["remaining_qty", "remaining_value"])
            value += part
            need -= take
            if need == 0:
                break
        if need > 0:
            what = f"lot {lot.lot_number} of {product.sku}" if lot is not None else f"{product.sku}"
            raise BusinessError(f"Cost layers for {what} are short by {need}.", "layers_short")
    else:
        if qty == val.quantity:
            value = val.value  # last units take the remaining value: no rounding residue
        else:
            value = round_to(qty * val.value / val.quantity, decimals)
    unit_cost = (value / qty).quantize(Decimal("0.000001"))
    val.quantity -= qty
    val.value -= value
    val.save()
    quant.quantity -= qty
    quant.save()
    move = StockMove.objects.create(
        company=company, number=_number(company, on_date, False), date=on_date, move_type=move_type,
        product=product, warehouse=warehouse, quantity=qty, unit_cost=unit_cost, value=value, partner=partner,
        reference=reference, sales_line=sales_line, purchase_line=purchase_line,
        manufacturing_order=manufacturing_order, lot=lot, created_by=user)
    if serial_rows:
        StockMoveSerial.objects.bulk_create([StockMoveSerial(move=move, serial=s) for s in serial_rows])
    _finalize_outbound_serials(serial_rows, move_type)
    _post(company, move, counter_account, _inventory_account(product), user, partner)
    audit.record("stock_out", move, {"product": product.sku, "qty": qty, "value": value})
    _emit(company, move)
    return move


@transaction.atomic
def adjust(company, product, warehouse, counted_qty, on_date, user, *, unit_cost=None, reason=""):
    """Physical count: set on-hand to `counted_qty`. Gains are valued at `unit_cost` or current average."""
    st = gl.settings_for(company)
    acc = gl.require_account(st.inventory_adjustment_account, "inventory adjustment")
    quant = _lock_quant(company, product, warehouse)
    diff = D(counted_qty) - quant.quantity
    if diff == 0:
        raise BusinessError("Counted quantity equals quantity on hand; nothing to adjust.", "no_difference")
    ref = f"Count adjustment{': ' + reason if reason else ''}"
    if diff > 0:
        cost = D(unit_cost) if unit_cost is not None else _lock_valuation(company, product).average_cost
        if not cost:
            cost = product.purchase_price
        return receive(company, product, warehouse, diff, cost, on_date, user, counter_account=acc,
                       move_type=MoveType.ADJUST_IN, reference=ref)
    return issue(company, product, warehouse, -diff, on_date, user, counter_account=acc,
                 move_type=MoveType.ADJUST_OUT, reference=ref)


@transaction.atomic
def transfer(company, product, src, dst, qty, on_date, user, reference=""):
    """Move stock between warehouses. Valuation is company-wide, so no GL impact."""
    _check(company, product, src, qty)
    if src.pk == dst.pk or dst.company_id != company.pk:
        raise BusinessError("Choose two different warehouses of this company.", "invalid_transfer")
    qty = D(qty)
    a = _lock_quant(company, product, src)
    if a.quantity < qty:
        raise BusinessError(f"Insufficient stock in {src.code}: {a.quantity}.", "insufficient_stock")
    b = _lock_quant(company, product, dst)
    a.quantity -= qty
    b.quantity += qty
    a.save()
    b.save()
    out = StockMove.objects.create(company=company, number=_number(company, on_date, False), date=on_date,
                                   move_type=MoveType.TRANSFER_OUT, product=product, warehouse=src, quantity=qty,
                                   reference=reference or f"To {dst.code}", created_by=user)
    StockMove.objects.create(company=company, number=_number(company, on_date, True), date=on_date,
                             move_type=MoveType.TRANSFER_IN, product=product, warehouse=dst, quantity=qty,
                             reference=reference or f"From {src.code}", created_by=user)
    audit.record("stock_transfer", out, {"product": product.sku, "qty": qty, "from": src.code, "to": dst.code})
    return out


def _emit(company, move):
    from apps.integrations.events import emit
    emit(company, "stock.moved", {"id": move.pk, "number": move.number, "type": move.move_type,
                                  "product": move.product.sku, "warehouse": move.warehouse.code,
                                  "quantity": move.quantity, "value": move.value})


def lot_genealogy(lot):
    """Trace a lot forward (which finished-good lots it was consumed into, via manufacturing orders)
    and backward (which input lots were consumed into it, if it is itself a manufactured output).
    Traceability is derived purely from StockMove.lot + StockMove.manufacturing_order -- no separate
    genealogy table is needed since every consumption/production move already carries both."""
    consumed_in = (StockMove.objects.filter(lot=lot, move_type=MoveType.PRODUCTION_CONSUME)
                   .select_related("manufacturing_order").order_by("date", "id"))
    mo_ids_consumed_into = {m.manufacturing_order_id for m in consumed_in if m.manufacturing_order_id}
    produced_from = (StockMove.objects.filter(lot=lot, move_type=MoveType.PRODUCTION_OUTPUT)
                     .select_related("manufacturing_order").order_by("date", "id"))
    mo_ids_produced_by = {m.manufacturing_order_id for m in produced_from if m.manufacturing_order_id}

    forward = list(StockMove.objects.filter(manufacturing_order_id__in=mo_ids_consumed_into,
                                            move_type=MoveType.PRODUCTION_OUTPUT)
                   .select_related("lot", "manufacturing_order", "product").order_by("date", "id"))
    backward = list(StockMove.objects.filter(manufacturing_order_id__in=mo_ids_produced_by,
                                             move_type=MoveType.PRODUCTION_CONSUME)
                    .select_related("lot", "manufacturing_order", "product").order_by("date", "id"))

    def _row(m):
        return {"move_id": m.pk, "mo_number": m.manufacturing_order.number if m.manufacturing_order_id else None,
                "product": m.product.sku, "lot_number": m.lot.lot_number if m.lot_id else None,
                "quantity": str(m.quantity), "date": str(m.date)}

    return {"lot": {"id": lot.pk, "product": lot.product.sku, "lot_number": lot.lot_number,
                    "expiry_date": str(lot.expiry_date) if lot.expiry_date else None},
            "consumed_into": [_row(m) for m in consumed_in],
            "produced_from": [_row(m) for m in produced_from],
            "forward_traceability": [_row(m) for m in forward],
            "backward_traceability": [_row(m) for m in backward]}


def reorder_suggestions(company):
    """Simple (un-timephased) MRP gap analysis: for every stockable product, net available = on-hand
    + incoming (open PO lines + open MO output) - outstanding demand (open SO lines + open MO
    component needs). Where net available falls below `Product.reorder_level`, suggest replenishing
    up to `Product.reorder_qty` (0 means just reach the reorder level itself)."""
    from apps.manufacturing.models import ManufacturingOrder, MoComponent, MoState
    from apps.purchasing.models import PurchaseOrderLine
    from apps.sales.models import OrderState, SalesOrderLine

    from .models import Product

    OPEN_MO_STATES = (MoState.DRAFT, MoState.CONFIRMED, MoState.IN_PROGRESS)
    on_hand = {v.product_id: v.quantity for v in ProductValuation.objects.filter(company=company)}

    incoming_po = {}
    for line in (PurchaseOrderLine.objects.filter(order__company=company, order__state=OrderState.CONFIRMED)
                 .exclude(quantity__lte=F("qty_received")).select_related("product")):
        incoming_po[line.product_id] = incoming_po.get(line.product_id, ZERO) + (line.quantity - line.qty_received)

    outgoing_so = {}
    for line in (SalesOrderLine.objects.filter(order__company=company, order__state=OrderState.CONFIRMED)
                 .exclude(quantity__lte=F("qty_delivered")).select_related("product")):
        outgoing_so[line.product_id] = outgoing_so.get(line.product_id, ZERO) + (line.quantity - line.qty_delivered)

    incoming_mo = {}
    for mo in ManufacturingOrder.objects.filter(company=company, state__in=OPEN_MO_STATES):
        remaining = mo.quantity - mo.produced_qty
        if remaining > 0:
            incoming_mo[mo.product_id] = incoming_mo.get(mo.product_id, ZERO) + remaining

    outgoing_mo_component = {}
    for comp in (MoComponent.objects.filter(mo__company=company, mo__state__in=OPEN_MO_STATES)
                 .select_related("product")):
        remaining = comp.planned_qty - comp.consumed_qty
        if remaining > 0:
            outgoing_mo_component[comp.product_id] = (outgoing_mo_component.get(comp.product_id, ZERO) + remaining)

    rows = []
    products = Product.objects.filter(company=company, type="stock", active=True).select_related("category")
    for p in products:
        if not p.reorder_level:
            continue
        net = (on_hand.get(p.pk, ZERO) + incoming_po.get(p.pk, ZERO) + incoming_mo.get(p.pk, ZERO)
               - outgoing_so.get(p.pk, ZERO) - outgoing_mo_component.get(p.pk, ZERO))
        if net < p.reorder_level:
            target = p.reorder_qty if p.reorder_qty > 0 else p.reorder_level
            rows.append({"product_id": p.pk, "sku": p.sku, "name": p.name, "on_hand": str(on_hand.get(p.pk, ZERO)),
                        "incoming": str(incoming_po.get(p.pk, ZERO) + incoming_mo.get(p.pk, ZERO)),
                        "outstanding_demand": str(outgoing_so.get(p.pk, ZERO) + outgoing_mo_component.get(p.pk, ZERO)),
                        "net_available": str(net), "reorder_level": str(p.reorder_level),
                        "suggested_qty": str(target - net)})
    return rows


def serial_history(serial):
    """Trace a single serial number's movements (receipts, consumption, delivery, scrap) in order."""
    links = (StockMoveSerial.objects.filter(serial=serial)
             .select_related("move", "move__manufacturing_order", "move__product").order_by("move__date", "move__id"))

    def _row(link):
        m = link.move
        return {"move_id": m.pk, "move_number": m.number, "move_type": m.move_type, "date": str(m.date),
                "product": m.product.sku, "warehouse": m.warehouse.code,
                "mo_number": m.manufacturing_order.number if m.manufacturing_order_id else None}

    return {"serial": {"id": serial.pk, "product": serial.product.sku, "serial_number": serial.serial_number,
                       "status": serial.status, "lot_number": serial.lot.lot_number if serial.lot_id else None},
            "movements": [_row(l) for l in links]}


def valuation_report(company):
    rows, total = [], ZERO
    for v in (ProductValuation.objects.filter(company=company).select_related("product", "product__category")
              .order_by("product__sku")):
        if not v.quantity and not v.value:
            continue
        total += v.value
        rows.append({"product_id": v.product_id, "sku": v.product.sku, "name": v.product.name,
                     "category": v.product.category.name, "cost_method": v.product.category.cost_method,
                     "quantity": str(v.quantity), "average_cost": str(round_to(v.average_cost, 4)),
                     "value": str(v.value)})
    return {"rows": rows, "total_value": str(total)}


@transaction.atomic
def post_landed_cost(lc, user):
    """Allocate the landed cost over the selected receipts. The share of goods still on hand goes to
    Inventory (raising the cost layers / average), the share already sold goes straight to COGS."""
    from .models import LandedCost
    lc = LandedCost.objects.select_for_update().get(pk=lc.pk)
    company = lc.company
    if lc.state != "draft":
        raise BusinessError("Landed cost already posted.", "invalid_state")
    moves = list(lc.moves.select_related("product", "product__category"))
    if not moves:
        raise BusinessError("Select the receipts the cost relates to.", "no_moves")
    for m in moves:
        if m.company_id != company.pk or m.move_type not in (MoveType.RECEIPT, MoveType.OPENING):
            raise BusinessError(f"{m}: only goods receipts can carry landed costs.", "invalid_move")
    decimals = gl.base_decimals(company)
    basis = [(m.value if lc.allocation == "value" else m.quantity) for m in moves]
    total_basis = sum(basis, ZERO)
    if not total_basis:
        raise BusinessError("Selected receipts have no value/quantity to allocate on.", "zero_basis")
    counter = lc.counter_account or gl.require_account(gl.settings_for(company).landed_cost_clearing_account,
                                                       "landed costs clearing")
    specs, allocated = [], ZERO
    for i, (m, b) in enumerate(zip(moves, basis)):
        share = lc.amount - allocated if i == len(moves) - 1 else round_to(lc.amount * b / total_basis, decimals)
        allocated += share
        val = _lock_valuation(company, m.product)
        layer = CostLayer.objects.select_for_update().get(move=m)
        if m.product.category.cost_method == CostMethod.FIFO:
            ratio = layer.remaining_qty / layer.quantity
        else:
            ratio = min(Decimal(1), val.quantity / m.quantity) if m.quantity else ZERO
        on_hand = round_to(share * ratio, decimals)
        sold = share - on_hand
        layer.value += share
        if m.product.category.cost_method == CostMethod.FIFO:
            layer.remaining_value += on_hand
        layer.save(update_fields=["value", "remaining_value"])
        val.value += on_hand
        val.save(update_fields=["value"])
        if on_hand:
            specs.append(gl.LineSpec(account=_inventory_account(m.product), debit=on_hand, product=m.product,
                                     description=f"Landed cost {m.product.sku} ({m.number})"))
        if sold:
            specs.append(gl.LineSpec(account=gl.require_account(m.product.category.cogs_account, "COGS"), debit=sold,
                                     product=m.product, description=f"Landed cost on goods already sold {m.product.sku}"))
    specs.append(gl.LineSpec(account=counter, credit=lc.amount, description=lc.description))
    lc.number = Sequence.next_number(company, "doc:LC", lc.date.year, "LC")
    entry = gl.create_entry(company, gl.journal_for(company, "inventory_journal", "inventory"), lc.date, specs,
                            user=user, reference=lc.number, narration=lc.description, source=("landed_cost", lc.pk))
    gl.post_entry(entry, user, allow_control=True)
    lc.state, lc.journal_entry = "posted", entry
    lc.save()
    audit.record("post", lc, {"amount": lc.amount, "moves": [m.number for m in moves]})
    return lc
