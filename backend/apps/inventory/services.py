"""Perpetual inventory with FIFO / weighted-average costing.

Each inbound move creates a cost layer and posts   Dr Inventory / Cr <counter account>.
Each outbound move is costed (FIFO layers or average) and posts   Dr <counter account> / Cr Inventory.
The valuation row of the product is locked (SELECT ... FOR UPDATE) for the whole move, so concurrent
moves on the same product are serialized and costs stay correct.
"""
from decimal import Decimal

from django.db import transaction

from apps.core import audit
from apps.core.exceptions import BusinessError
from apps.core.models import Sequence
from apps.core.money import ZERO, D, round_to
from apps.ledger import services as gl

from .models import INBOUND, CostLayer, CostMethod, MoveType, ProductValuation, StockMove, StockQuant


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
            move_type=MoveType.RECEIPT, partner=None, reference="", purchase_line=None, sales_line=None):
    """Bring stock in at `unit_cost` (base currency). Credits `counter_account`."""
    _check(company, product, warehouse, qty)
    if move_type not in INBOUND:
        raise BusinessError("Not an inbound move type.", "invalid_move")
    qty, unit_cost = D(qty), D(unit_cost)
    if unit_cost < 0:
        raise BusinessError("Unit cost cannot be negative.", "invalid_cost")
    val = _lock_valuation(company, product)
    quant = _lock_quant(company, product, warehouse)
    value = round_to(qty * unit_cost, gl.base_decimals(company))
    move = StockMove.objects.create(
        company=company, number=_number(company, on_date, True), date=on_date, move_type=move_type,
        product=product, warehouse=warehouse, quantity=qty, unit_cost=unit_cost, value=value, partner=partner,
        reference=reference, purchase_line=purchase_line, sales_line=sales_line, created_by=user)
    CostLayer.objects.create(company=company, product=product, move=move, date=on_date, quantity=qty,
                             unit_cost=unit_cost, value=value, remaining_qty=qty, remaining_value=value)
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
          move_type=MoveType.DELIVERY, partner=None, reference="", sales_line=None, purchase_line=None):
    """Take stock out, costed by the product's method. Debits `counter_account` (e.g. COGS)."""
    _check(company, product, warehouse, qty)
    if move_type in INBOUND:
        raise BusinessError("Not an outbound move type.", "invalid_move")
    qty = D(qty)
    val = _lock_valuation(company, product)
    quant = _lock_quant(company, product, warehouse)
    if quant.quantity < qty:
        raise BusinessError(f"Insufficient stock of {product.sku} in {warehouse.code}: "
                            f"on hand {quant.quantity}, requested {qty}.", "insufficient_stock")
    decimals = gl.base_decimals(company)
    if product.category.cost_method == CostMethod.FIFO:
        value, need = ZERO, qty
        for layer in (CostLayer.objects.select_for_update()
                      .filter(product=product, remaining_qty__gt=0).order_by("date", "id")):
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
            raise BusinessError(f"Cost layers for {product.sku} are short by {need}.", "layers_short")
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
        reference=reference, sales_line=sales_line, purchase_line=purchase_line, created_by=user)
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
