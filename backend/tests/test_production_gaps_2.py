"""Second round of production module enhancements: finite-capacity scheduling with alternate routings
and calendar exceptions (Gap 1/8), simple MRP reorder suggestions (Gap 2), subcontracting (Gap 3),
the QC inspection gate (Gap 4), unit-level serial tracking (Gap 5), backflush costing (Gap 6), and
co-products / by-products (Gap 7)."""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.core.exceptions import BusinessError
from apps.inventory import services as stock
from apps.inventory.models import MoveType, Product, SerialNumber, SerialStatus
from apps.manufacturing import services as mrp
from apps.manufacturing.models import (Bom, BomByproduct, BomComponent, BomOperation, ManufacturingOrder,
                                       WorkCenter, WorkCenterCalendarException)

from .conftest import D


def client(env, user):
    c = APIClient()
    c.force_authenticate(user)
    c.credentials(HTTP_X_COMPANY=str(env.c.pk))
    return c


# ------------------------------------------------------------------ Gap: capacity planning / scheduling

def test_schedule_operations_spans_multiple_days_and_falls_back_to_alternate_work_center(eg):
    wc_a = WorkCenter.objects.create(company=eg.c, code="WC-A", name="Primary", capacity_minutes_per_day=D(240),
                                     working_weekdays="0,1,2,3,4,5,6")
    wc_b = WorkCenter.objects.create(company=eg.c, code="WC-B", name="Overflow", capacity_minutes_per_day=D(480),
                                     working_weekdays="0,1,2,3,4,5,6")
    part = Product.objects.create(company=eg.c, sku="FG-SCHED", name="Scheduled part", category=eg.fifo)
    bom = Bom.objects.create(company=eg.c, code="BOM-SCHED", product=part, quantity=1)
    # 480 = exactly two full days at 240/day, so the operation's own last day is itself fully booked
    # (its "remainder" on that day is the whole day's capacity) -- that's what forces the next
    # operation to fall back to the alternate work center instead of squeezing onto a part-day gap.
    BomOperation.objects.create(bom=bom, sequence=10, name="Cut", work_center=wc_a, minutes=D(480))
    op2 = BomOperation.objects.create(bom=bom, sequence=20, name="Finish", work_center=wc_a, minutes=D(100))
    op2.alternate_work_centers.set([wc_b])

    mo = ManufacturingOrder.objects.create(company=eg.c, product=part, bom=bom, quantity=D(1), warehouse=eg.wh,
                                           planned_date=eg.d(1, 10))
    mrp.confirm(mo, eg.cfo)

    cut = mo.operations.get(name="Cut")
    finish = mo.operations.get(name="Finish")
    # 480 minutes at 240/day: day0 absorbs 240 (240 left), day1 absorbs the remaining 240 -- two
    # calendar days, ending on day0 + 1.
    assert cut.scheduled_start == eg.d(1, 10) and cut.scheduled_end == eg.d(1, 11)
    # "Finish" starts the day "Cut" ends; WC-A is still fully booked that day (its own last-day
    # remainder used the whole 240/day capacity), so the scheduler must fall back to the alternate.
    assert finish.work_center_id == wc_b.pk
    assert finish.scheduled_start == eg.d(1, 11) and finish.scheduled_end == eg.d(1, 11)


def test_schedule_operations_skips_calendar_exception_holiday(eg):
    wc = WorkCenter.objects.create(company=eg.c, code="WC-HOL", name="With holiday",
                                   capacity_minutes_per_day=D(240), working_weekdays="0,1,2,3,4,5,6")
    WorkCenterCalendarException.objects.create(company=eg.c, work_center=wc, date=eg.d(8, 2),
                                               available_minutes=D(0), notes="Holiday")
    part = Product.objects.create(company=eg.c, sku="FG-SCHED2", name="Scheduled part 2", category=eg.fifo)
    bom = Bom.objects.create(company=eg.c, code="BOM-SCHED2", product=part, quantity=1)
    BomOperation.objects.create(bom=bom, name="Mill", work_center=wc, minutes=D(400))
    mo = ManufacturingOrder.objects.create(company=eg.c, product=part, bom=bom, quantity=D(1), warehouse=eg.wh,
                                           planned_date=eg.d(8, 1))
    mrp.confirm(mo, eg.cfo)

    op = mo.operations.get(name="Mill")
    # Day 1 (Aug 1) absorbs 240 (160 left); day 2 (Aug 2) is a declared holiday (0 minutes), so the
    # remaining 160 only gets absorbed on day 3 (Aug 3) -- the holiday must not count as a working day.
    assert op.scheduled_start == eg.d(8, 1) and op.scheduled_end == eg.d(8, 3)


def test_reschedule_action_reruns_scheduler(eg):
    wc = WorkCenter.objects.create(company=eg.c, code="WC-RS", name="Reschedule test",
                                   capacity_minutes_per_day=D(240), working_weekdays="0,1,2,3,4,5,6")
    part = Product.objects.create(company=eg.c, sku="FG-RESCHED", name="Resched part", category=eg.fifo)
    bom = Bom.objects.create(company=eg.c, code="BOM-RESCHED", product=part, quantity=1)
    BomOperation.objects.create(bom=bom, name="Op", work_center=wc, minutes=D(100))
    mo = ManufacturingOrder.objects.create(company=eg.c, product=part, bom=bom, quantity=D(1), warehouse=eg.wh,
                                           planned_date=eg.d(9, 1))
    mrp.confirm(mo, eg.cfo)
    op = mo.operations.get()
    assert op.scheduled_start == eg.d(9, 1)

    # Adding a holiday on the planned start day and re-running the scheduler should push it out.
    WorkCenterCalendarException.objects.create(company=eg.c, work_center=wc, date=eg.d(9, 1),
                                               available_minutes=D(0))
    mrp.reschedule(mo)
    op.refresh_from_db()
    # The operation still "starts" on its original day (the scheduler never pulls the start earlier
    # than the previous operation's end), but it can't finish until a day with free capacity.
    assert op.scheduled_start == eg.d(9, 1) and op.scheduled_end == eg.d(9, 2)


def test_work_center_calendar_exception_api(eg):
    wc = WorkCenter.objects.create(company=eg.c, code="WC-API", name="API test",
                                   capacity_minutes_per_day=D(240), working_weekdays="0,1,2,3,4,5,6")
    cfo = client(eg, eg.cfo)
    r = cfo.post("/api/work-center-calendar-exceptions/", {"work_center": wc.pk, "date": str(eg.d(9, 10)),
                                                           "available_minutes": "0", "notes": "Holiday"},
                format="json")
    assert r.status_code == 201, r.content
    assert WorkCenterCalendarException.objects.filter(work_center=wc, date=eg.d(9, 10)).exists()


# ------------------------------------------------------------------ Gap: MRP reorder suggestions

def test_reorder_suggestions_flags_products_below_level(eg):
    widget = Product.objects.create(company=eg.c, sku="RM-WIDGET", name="Widget", category=eg.raw,
                                    reorder_level=D(50), reorder_qty=D(200))
    stock.receive(eg.c, widget, eg.wh, 10, 5, eg.d(1, 1), eg.cfo, counter_account=eg.A["320102"],
                 move_type=MoveType.OPENING)
    rows = stock.reorder_suggestions(eg.c)
    row = next(r for r in rows if r["sku"] == "RM-WIDGET")
    assert D(row["on_hand"]) == D(10) and D(row["net_available"]) == D(10) and D(row["suggested_qty"]) == D(190)

    # Above the reorder level, it must not be suggested.
    stock.receive(eg.c, widget, eg.wh, 50, 5, eg.d(1, 2), eg.cfo, counter_account=eg.A["320102"],
                 move_type=MoveType.OPENING)
    rows = stock.reorder_suggestions(eg.c)
    assert not any(r["sku"] == "RM-WIDGET" for r in rows)


def test_reorder_suggestions_nets_incoming_po_and_outstanding_so(eg):
    from apps.purchasing import services as purchasing
    from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine
    from apps.sales import services as sales
    from apps.sales.models import SalesOrder, SalesOrderLine

    widget = Product.objects.create(company=eg.c, sku="RM-WIDGET2", name="Widget 2", category=eg.raw,
                                    reorder_level=D(50))
    stock.receive(eg.c, widget, eg.wh, 60, 5, eg.d(1, 1), eg.cfo, counter_account=eg.A["320102"],
                 move_type=MoveType.OPENING)

    po = PurchaseOrder.objects.create(company=eg.c, partner=eg.vendor, date=eg.d(1, 2), currency_id="EGP",
                                      warehouse=eg.wh)
    PurchaseOrderLine.objects.create(order=po, product=widget, quantity=20, unit_price=5, tax=eg.T["VAT14-P"])
    purchasing.confirm(po, eg.cfo)

    so = SalesOrder.objects.create(company=eg.c, partner=eg.customer, date=eg.d(1, 2), currency_id="EGP",
                                   warehouse=eg.wh)
    SalesOrderLine.objects.create(order=so, product=widget, quantity=50, unit_price=10, tax=eg.T["VAT14-S"])
    sales.confirm(so, eg.cfo)

    rows = stock.reorder_suggestions(eg.c)
    row = next(r for r in rows if r["sku"] == "RM-WIDGET2")
    # 60 on hand + 20 incoming PO - 50 outstanding SO = 30, below the 50 reorder level.
    assert D(row["net_available"]) == D(30) and D(row["incoming"]) == D(20) and D(row["outstanding_demand"]) == D(50)
    assert D(row["suggested_qty"]) == D(20)  # target = reorder_level (reorder_qty is 0) - net = 50 - 30


def test_reorder_suggestions_api_endpoint(eg):
    Product.objects.create(company=eg.c, sku="RM-WIDGET3", name="Widget 3", category=eg.raw, reorder_level=D(5))
    cfo = client(eg, eg.cfo)
    r = cfo.get("/api/products/reorder-suggestions/")
    assert r.status_code == 200, r.content
    assert any(row["sku"] == "RM-WIDGET3" for row in r.data)


# ------------------------------------------------------------------ Gap: subcontracting

def test_subcontract_cost_requires_subcontracted_work_center(eg):
    product = Product.objects.create(company=eg.c, sku="FG-SUB", name="Subcontracted part", category=eg.fifo)
    wc = WorkCenter.objects.create(company=eg.c, code="PLT", name="Plating")
    bom = Bom.objects.create(company=eg.c, code="BOM-SUB", product=product, quantity=1)
    BomOperation.objects.create(bom=bom, name="Plate", work_center=wc, minutes=D(60))
    mo = ManufacturingOrder.objects.create(company=eg.c, product=product, bom=bom, quantity=D(10), warehouse=eg.wh,
                                           planned_date=eg.d(2, 1))
    mrp.confirm(mo, eg.cfo)
    op = mo.operations.get(name="Plate")
    with pytest.raises(BusinessError, match="not_subcontracted|subcontracted work center"):
        mrp.record_subcontract_cost(mo, eg.cfo, op.pk, 500, eg.d(2, 2))


def test_subcontract_cost_books_wip_and_clearing(eg):
    from django.db.models import Sum

    from apps.ledger.models import JournalLine

    product = Product.objects.create(company=eg.c, sku="FG-SUB2", name="Subcontracted part 2", category=eg.fifo)
    wc = WorkCenter.objects.create(company=eg.c, code="PLT2", name="Plating (ext)", is_subcontracted=True,
                                   default_vendor=eg.vendor)
    bom = Bom.objects.create(company=eg.c, code="BOM-SUB2", product=product, quantity=1)
    BomOperation.objects.create(bom=bom, name="Plate", work_center=wc, minutes=D(60))
    mo = ManufacturingOrder.objects.create(company=eg.c, product=product, bom=bom, quantity=D(10), warehouse=eg.wh,
                                           planned_date=eg.d(2, 1))
    mrp.confirm(mo, eg.cfo)
    op = mo.operations.get(name="Plate")

    with pytest.raises(BusinessError, match="invalid_amount|charge"):
        mrp.record_subcontract_cost(mo, eg.cfo, op.pk, 0, eg.d(2, 2))

    mrp.record_subcontract_cost(mo, eg.cfo, op.pk, 500, eg.d(2, 2))
    mo.refresh_from_db()
    op.refresh_from_db()
    assert op.subcontract_cost == D(500)
    assert mo.overhead_cost == D(500)
    assert mo.state == "in_progress"

    clearing = eg.A["210105"]
    agg = JournalLine.objects.filter(company=eg.c, account=clearing, posted=True).aggregate(d=Sum("debit"),
                                                                                             c=Sum("credit"))
    assert (agg["c"] or 0) - (agg["d"] or 0) == D(500)


# ------------------------------------------------------------------ Gap: QC inspection gate

def test_produce_blocked_until_qc_pass_then_released(eg):
    steel = Product.objects.create(company=eg.c, sku="RM-STEELQC", name="Steel", category=eg.fifo)
    stock.receive(eg.c, steel, eg.wh, 100, 10, eg.d(3, 1), eg.cfo, counter_account=eg.A["320102"],
                 move_type=MoveType.OPENING)
    gadget = Product.objects.create(company=eg.c, sku="FG-GADGET", name="Gadget", category=eg.fifo,
                                    requires_qc=True)
    bom = Bom.objects.create(company=eg.c, code="BOM-GADGET", product=gadget, quantity=1)
    BomComponent.objects.create(bom=bom, product=steel, quantity=D(2))
    mo = ManufacturingOrder.objects.create(company=eg.c, product=gadget, bom=bom, quantity=D(10), warehouse=eg.wh,
                                           planned_date=eg.d(3, 5))
    mrp.confirm(mo, eg.cfo)
    mrp.consume(mo, eg.cfo, on_date=eg.d(3, 5))

    with pytest.raises(BusinessError, match="qc_not_passed|quality inspection"):
        mrp.produce(mo, eg.cfo, 10, on_date=eg.d(3, 6))

    with pytest.raises(BusinessError, match="invalid_qty|passed and/or failed"):
        mrp.inspect(mo, eg.cfo, qty_passed=0, qty_failed=0, on_date=eg.d(3, 6))

    mrp.inspect(mo, eg.cfo, qty_passed=6, qty_failed=1, on_date=eg.d(3, 6))
    with pytest.raises(BusinessError, match="qc_not_passed|quality inspection"):
        mrp.produce(mo, eg.cfo, 10, on_date=eg.d(3, 6))

    mo = mrp.produce(mo, eg.cfo, 6, on_date=eg.d(3, 6))
    assert mo.produced_qty == D(6)


def test_inspect_requires_qc_product(eg):
    plain = Product.objects.create(company=eg.c, sku="FG-PLAIN", name="Plain", category=eg.fifo)
    bom = Bom.objects.create(company=eg.c, code="BOM-PLAIN", product=plain, quantity=1)
    mo = ManufacturingOrder.objects.create(company=eg.c, product=plain, bom=bom, quantity=D(5), warehouse=eg.wh,
                                           planned_date=eg.d(3, 1))
    mrp.confirm(mo, eg.cfo)
    with pytest.raises(BusinessError, match="qc_not_required|does not require"):
        mrp.inspect(mo, eg.cfo, qty_passed=5, on_date=eg.d(3, 2))


# ------------------------------------------------------------------ Gap: serial number tracking

def test_serial_tracked_product_requires_matching_serial_count(eg):
    drive = Product.objects.create(company=eg.c, sku="FG-DRIVE", name="Hard drive", category=eg.fifo,
                                   track_serials=True)
    with pytest.raises(BusinessError, match="serial_count_mismatch|serial-tracked"):
        stock.receive(eg.c, drive, eg.wh, 3, 100, eg.d(4, 1), eg.cfo, counter_account=eg.A["320102"],
                     move_type=MoveType.OPENING, serials=["SN-1", "SN-2"])

    stock.receive(eg.c, drive, eg.wh, 3, 100, eg.d(4, 1), eg.cfo, counter_account=eg.A["320102"],
                 move_type=MoveType.OPENING, serials=["SN-1", "SN-2", "SN-3"])
    assert SerialNumber.objects.filter(company=eg.c, product=drive, status=SerialStatus.IN_STOCK).count() == 3

    stock.issue(eg.c, drive, eg.wh, 2, eg.d(4, 2), eg.cfo, counter_account=eg.A["510101"], serials=["SN-1", "SN-2"])
    assert SerialNumber.objects.get(serial_number="SN-1").status == SerialStatus.DELIVERED
    assert SerialNumber.objects.get(serial_number="SN-3").status == SerialStatus.IN_STOCK

    with pytest.raises(BusinessError, match="unknown_serial|Unknown serial"):
        stock.issue(eg.c, drive, eg.wh, 1, eg.d(4, 3), eg.cfo, counter_account=eg.A["510101"], serials=["SN-404"])

    with pytest.raises(BusinessError, match="serial_not_available|not in stock"):
        stock.issue(eg.c, drive, eg.wh, 1, eg.d(4, 3), eg.cfo, counter_account=eg.A["510101"], serials=["SN-1"])


def test_serial_history_traces_movements_and_api_endpoint(eg):
    drive = Product.objects.create(company=eg.c, sku="FG-DRIVE2", name="Drive", category=eg.fifo,
                                   track_serials=True)
    stock.receive(eg.c, drive, eg.wh, 1, 100, eg.d(4, 1), eg.cfo, counter_account=eg.A["320102"],
                 move_type=MoveType.OPENING, serials=["SN-X1"])
    stock.issue(eg.c, drive, eg.wh, 1, eg.d(4, 2), eg.cfo, counter_account=eg.A["510101"], serials=["SN-X1"])
    serial = SerialNumber.objects.get(company=eg.c, product=drive, serial_number="SN-X1")

    history = stock.serial_history(serial)
    assert [m["move_type"] for m in history["movements"]] == ["opening", "delivery"]

    cfo = client(eg, eg.cfo)
    r = cfo.get(f"/api/serial-numbers/{serial.pk}/history/")
    assert r.status_code == 200, r.content
    assert len(r.data["movements"]) == 2


def test_manufacturing_serial_tracked_finished_good(eg):
    steel = Product.objects.create(company=eg.c, sku="RM-STEELSN", name="Steel", category=eg.fifo)
    stock.receive(eg.c, steel, eg.wh, 100, 10, eg.d(4, 1), eg.cfo, counter_account=eg.A["320102"],
                 move_type=MoveType.OPENING)
    laptop = Product.objects.create(company=eg.c, sku="FG-LAPTOP", name="Laptop", category=eg.fifo,
                                    track_serials=True)
    bom = Bom.objects.create(company=eg.c, code="BOM-LAPTOP", product=laptop, quantity=1)
    BomComponent.objects.create(bom=bom, product=steel, quantity=D(1))
    mo = ManufacturingOrder.objects.create(company=eg.c, product=laptop, bom=bom, quantity=D(2), warehouse=eg.wh,
                                           planned_date=eg.d(4, 5))
    mrp.confirm(mo, eg.cfo)
    mrp.consume(mo, eg.cfo, on_date=eg.d(4, 5))
    mrp.produce(mo, eg.cfo, 2, on_date=eg.d(4, 6), finish=True, serials=["LAP-001", "LAP-002"])

    assert SerialNumber.objects.filter(company=eg.c, product=laptop, status=SerialStatus.IN_STOCK).count() == 2


# ------------------------------------------------------------------ Gap: backflush costing

def test_backflush_bom_auto_consumes_components_on_produce(eg):
    screw = Product.objects.create(company=eg.c, sku="RM-SCREW", name="Screw", category=eg.avg)
    stock.receive(eg.c, screw, eg.wh, 1000, 1, eg.d(5, 1), eg.cfo, counter_account=eg.A["320102"],
                 move_type=MoveType.OPENING)
    toy = Product.objects.create(company=eg.c, sku="FG-TOY", name="Toy", category=eg.fifo)
    bom = Bom.objects.create(company=eg.c, code="BOM-TOY", product=toy, quantity=1, backflush=True)
    BomComponent.objects.create(bom=bom, product=screw, quantity=D(4))
    mo = ManufacturingOrder.objects.create(company=eg.c, product=toy, bom=bom, quantity=D(10), warehouse=eg.wh,
                                           planned_date=eg.d(5, 5))
    mrp.confirm(mo, eg.cfo)

    # No explicit consume() call -- produce() must backflush the component shortfall itself.
    mo = mrp.produce(mo, eg.cfo, 10, on_date=eg.d(5, 6), finish=True)
    comp = mo.components.get(product=screw)
    assert comp.consumed_qty == D(40) and mo.material_cost == D(40)
    assert mo.wip_balance == 0


def test_backflush_still_blocked_for_lot_tracked_component_until_manually_consumed(eg):
    batch_item = Product.objects.create(company=eg.c, sku="RM-BATCH", name="Batch input", category=eg.fifo,
                                        track_lots=True)
    stock.receive(eg.c, batch_item, eg.wh, 100, 2, eg.d(5, 1), eg.cfo, counter_account=eg.A["320102"],
                 move_type=MoveType.OPENING, lot_number="LOT-1")
    toy = Product.objects.create(company=eg.c, sku="FG-TOY2", name="Toy 2", category=eg.fifo)
    bom = Bom.objects.create(company=eg.c, code="BOM-TOY2", product=toy, quantity=1, backflush=True)
    BomComponent.objects.create(bom=bom, product=batch_item, quantity=D(1))
    mo = ManufacturingOrder.objects.create(company=eg.c, product=toy, bom=bom, quantity=D(5), warehouse=eg.wh,
                                           planned_date=eg.d(5, 5))
    mrp.confirm(mo, eg.cfo)

    with pytest.raises(BusinessError, match="lot_required|lot-tracked"):
        mrp.produce(mo, eg.cfo, 5, on_date=eg.d(5, 6), finish=True)

    comp = mo.components.get()
    mrp.consume(mo, eg.cfo, on_date=eg.d(5, 6), lots={comp.pk: "LOT-1"})
    mo = mrp.produce(mo, eg.cfo, 5, on_date=eg.d(5, 6), finish=True)
    comp.refresh_from_db()
    assert comp.consumed_qty == D(5)


# ------------------------------------------------------------------ Gap: co-products / by-products

def test_byproduct_credited_against_wip_reduces_main_product_cost(eg):
    wood = Product.objects.create(company=eg.c, sku="RM-WOOD", name="Wood", category=eg.avg)
    stock.receive(eg.c, wood, eg.wh, 100, 10, eg.d(6, 1), eg.cfo, counter_account=eg.A["320102"],
                 move_type=MoveType.OPENING)
    sawdust = Product.objects.create(company=eg.c, sku="BY-SAWDUST", name="Sawdust", category=eg.avg)
    table = Product.objects.create(company=eg.c, sku="FG-TABLE", name="Table", category=eg.fifo)
    bom = Bom.objects.create(company=eg.c, code="BOM-TABLE", product=table, quantity=1)
    BomComponent.objects.create(bom=bom, product=wood, quantity=D(2))
    BomByproduct.objects.create(bom=bom, product=sawdust, quantity=D(2), unit_value=D(1))
    mo = ManufacturingOrder.objects.create(company=eg.c, product=table, bom=bom, quantity=D(5), warehouse=eg.wh,
                                           planned_date=eg.d(6, 5))
    mrp.confirm(mo, eg.cfo)
    byprod = mo.byproducts.get(product=sawdust)
    assert byprod.planned_qty == D(10)  # 2 x 5

    mrp.consume(mo, eg.cfo, on_date=eg.d(6, 5))  # (2 x 5) wood units x average cost 10 = 100 material cost
    mo.refresh_from_db()
    assert mo.material_cost == D(100)

    mo = mrp.produce(mo, eg.cfo, 5, on_date=eg.d(6, 6), finish=True, byproducts={byprod.pk: 10})
    byprod.refresh_from_db()
    assert byprod.produced_qty == D(10) and byprod.value == D(10)  # 10 units x unit_value 1
    assert mo.byproduct_value == D(10)
    # WIP of 100, credited 10 for the by-product, leaves 90 absorbed into the main product.
    assert mo.output_value == D(90) and mo.wip_balance == 0
    assert stock._lock_valuation(eg.c, sawdust).quantity == D(10)


def test_manufacturing_order_api_full_gap_flow(eg):
    wc = WorkCenter.objects.create(company=eg.c, code="EXT", name="External plating", is_subcontracted=True,
                                   default_vendor=eg.vendor, capacity_minutes_per_day=D(480))
    steel = Product.objects.create(company=eg.c, sku="RM-STEELAPI", name="Steel", category=eg.fifo)
    stock.receive(eg.c, steel, eg.wh, 100, 10, eg.d(7, 1), eg.cfo, counter_account=eg.A["320102"],
                 move_type=MoveType.OPENING)
    part = Product.objects.create(company=eg.c, sku="FG-PARTAPI", name="Part", category=eg.fifo, requires_qc=True)
    bom = Bom.objects.create(company=eg.c, code="BOM-PARTAPI", product=part, quantity=1)
    BomComponent.objects.create(bom=bom, product=steel, quantity=D(2))
    BomOperation.objects.create(bom=bom, name="Plate", work_center=wc, minutes=D(60))

    cfo = client(eg, eg.cfo)
    r = cfo.post("/api/manufacturing-orders/", {"product": part.pk, "bom": bom.pk, "quantity": "5",
                                                "warehouse": eg.wh.pk, "planned_date": str(eg.d(7, 5))},
                format="json")
    assert r.status_code == 201, r.content
    mo_id = r.data["id"]

    r = cfo.post(f"/api/manufacturing-orders/{mo_id}/confirm/")
    assert r.status_code == 200, r.content
    op = r.data["operations"][0]

    r = cfo.post(f"/api/manufacturing-orders/{mo_id}/reschedule/")
    assert r.status_code == 200, r.content

    r = cfo.post(f"/api/manufacturing-orders/{mo_id}/subcontract/", {"operation": op["id"], "amount": "300"},
                format="json")
    assert r.status_code == 201, r.content

    r = cfo.post(f"/api/manufacturing-orders/{mo_id}/consume/")
    assert r.status_code == 200, r.content

    r = cfo.post(f"/api/manufacturing-orders/{mo_id}/produce/", {"quantity": "5"}, format="json")
    assert r.status_code == 422, r.content  # QC gate blocks: nothing inspected yet

    r = cfo.post(f"/api/manufacturing-orders/{mo_id}/inspect/", {"qty_passed": "5"}, format="json")
    assert r.status_code == 201, r.content

    r = cfo.post(f"/api/manufacturing-orders/{mo_id}/produce/", {"quantity": "5", "finish": True}, format="json")
    assert r.status_code == 200, r.content
    assert r.data["state"] == "done" and Decimal(r.data["produced_qty"]) == D(5)

    r = cfo.get(f"/api/work-centers/{wc.pk}/load/")
    assert r.status_code == 200, r.content
