"""Production module enhancements: BOM versioning (Gap 1), multi-level BOM / sub-assemblies (Gap 2),
and lot/batch traceability (Gap 4)."""
from decimal import Decimal

import pytest
from django.db import DatabaseError, IntegrityError, transaction
from rest_framework.test import APIClient

from apps.core.exceptions import BusinessError
from apps.inventory import services as stock
from apps.inventory.models import Lot, MoveType, Product
from apps.manufacturing import services as mrp
from apps.manufacturing.models import Bom, BomComponent, BomOperation, ManufacturingOrder, WorkCenter

from .conftest import D


def client(env, user):
    c = APIClient()
    c.force_authenticate(user)
    c.credentials(HTTP_X_COMPANY=str(env.c.pk))
    return c


# ------------------------------------------------------------------ Gap 1: BOM versioning

def test_only_one_active_bom_version_per_code(eg):
    cabinet = Product.objects.create(company=eg.c, sku="FG-CAB", name="Cabinet", category=eg.fifo)
    Bom.objects.create(company=eg.c, code="BOM-CAB", product=cabinet, quantity=1)
    with pytest.raises((DatabaseError, IntegrityError)):
        with transaction.atomic():
            Bom.objects.create(company=eg.c, code="BOM-CAB", product=cabinet, quantity=1, version=2, active=True)


def test_revise_bom_creates_new_version_and_deactivates_old(eg):
    steel = Product.objects.create(company=eg.c, sku="RM-STEEL", name="Steel sheet", category=eg.raw)
    cabinet = Product.objects.create(company=eg.c, sku="FG-CAB", name="Cabinet", category=eg.fifo)
    wc = WorkCenter.objects.create(company=eg.c, code="ASM", name="Assembly", labour_rate=D(120),
                                   overhead_rate=D(60))
    bom = Bom.objects.create(company=eg.c, code="BOM-CAB", product=cabinet, quantity=1)
    BomComponent.objects.create(bom=bom, product=steel, quantity=D(2))
    BomOperation.objects.create(bom=bom, name="Assemble", work_center=wc, minutes=D(30))

    v2 = mrp.revise_bom(bom, eg.cfo)
    bom.refresh_from_db()

    assert v2.version == 2 and v2.code == "BOM-CAB" and v2.revises_id == bom.pk and v2.active
    assert not bom.active
    assert v2.components.count() == 1 and v2.components.first().product_id == steel.pk
    assert v2.operations.count() == 1 and v2.operations.first().name == "Assemble"
    # Revising again only works from the (now) active version.
    with pytest.raises(BusinessError):
        mrp.revise_bom(bom, eg.cfo)


def test_bom_update_blocked_while_open_mo_but_revise_allowed(eg):
    steel = Product.objects.create(company=eg.c, sku="RM-STEEL", name="Steel sheet", category=eg.raw)
    cabinet = Product.objects.create(company=eg.c, sku="FG-CAB", name="Cabinet", category=eg.fifo)
    bom = Bom.objects.create(company=eg.c, code="BOM-CAB", product=cabinet, quantity=1)
    BomComponent.objects.create(bom=bom, product=steel, quantity=D(2))
    mo = ManufacturingOrder.objects.create(company=eg.c, product=cabinet, bom=bom, quantity=D(5), warehouse=eg.wh,
                                           planned_date=eg.d(3, 5))
    mrp.confirm(mo, eg.cfo)

    cfo = client(eg, eg.cfo)
    r = cfo.patch(f"/api/boms/{bom.pk}/", {"components": [{"product": steel.pk, "quantity": "3"}]}, format="json")
    assert r.status_code == 422, r.content
    assert "in_use" in str(r.data).lower() or "version" in str(r.data).lower()

    r = cfo.post(f"/api/boms/{bom.pk}/revise/")
    assert r.status_code == 201, r.content
    assert r.data["version"] == 2 and r.data["id"] != bom.pk


# ------------------------------------------------------------------ Gap 2: multi-level BOM

def test_planned_cost_rolls_up_through_subassembly(eg):
    rivet = Product.objects.create(company=eg.c, sku="RM-RIVET", name="Rivet", category=eg.raw)
    stock.receive(eg.c, rivet, eg.wh, 1000, 2, eg.d(1, 1), eg.cfo, counter_account=eg.A["320102"],
                 move_type=MoveType.OPENING)
    bracket = Product.objects.create(company=eg.c, sku="SA-BRACKET", name="Bracket (sub-assembly)",
                                     category=eg.fifo)
    bracket_bom = Bom.objects.create(company=eg.c, code="BOM-BRACKET", product=bracket, quantity=1)
    BomComponent.objects.create(bom=bracket_bom, product=rivet, quantity=D(10))  # 10 × 2 = 20 material

    cabinet = Product.objects.create(company=eg.c, sku="FG-CAB", name="Cabinet", category=eg.fifo)
    cabinet_bom = Bom.objects.create(company=eg.c, code="BOM-CAB", product=cabinet, quantity=1)
    BomComponent.objects.create(bom=cabinet_bom, product=bracket, quantity=D(3))  # 3 brackets per cabinet

    # The bracket itself has never been produced, so its average_cost would be 0 -- planned_cost must
    # recurse through the bracket's own BOM (3 × 20 = 60) instead of using that stale/zero figure.
    cost = mrp.planned_cost(cabinet_bom, 1)
    assert cost["material"] == D(60)


def test_bom_rejects_transitive_cycle(eg):
    prod_a = Product.objects.create(company=eg.c, sku="SA-A", name="A", category=eg.fifo)
    prod_b = Product.objects.create(company=eg.c, sku="SA-B", name="B", category=eg.fifo)
    bom_b = Bom.objects.create(company=eg.c, code="BOM-B", product=prod_b, quantity=1)
    BomComponent.objects.create(bom=bom_b, product=prod_a, quantity=D(1))  # B is built from A

    cfo = client(eg, eg.cfo)
    r = cfo.post("/api/boms/", {"code": "BOM-A", "product": prod_a.pk, "quantity": "1",
                                "components": [{"product": prod_b.pk, "quantity": "1"}]}, format="json")
    assert r.status_code == 400, r.content
    assert "circular" in str(r.data).lower()


def test_bom_allows_valid_subassembly_component(eg):
    """A product that is itself a manufactured sub-assembly (has its own active BOM) can be used as a
    component of a higher-level BOM -- genuine multi-level nesting, as opposed to a cycle."""
    rivet = Product.objects.create(company=eg.c, sku="RM-RIVET", name="Rivet", category=eg.raw)
    bracket = Product.objects.create(company=eg.c, sku="SA-BRACKET", name="Bracket", category=eg.fifo)
    Bom.objects.create(company=eg.c, code="BOM-BRACKET", product=bracket, quantity=1) \
        .components.create(product=rivet, quantity=D(1))
    cabinet = Product.objects.create(company=eg.c, sku="FG-CAB3", name="Cabinet", category=eg.fifo)

    cfo = client(eg, eg.cfo)
    r = cfo.post("/api/boms/", {"code": "BOM-CAB3", "product": cabinet.pk, "quantity": "1",
                                "components": [{"product": bracket.pk, "quantity": "3"}]}, format="json")
    assert r.status_code in (200, 201), r.content


# ------------------------------------------------------------------ Gap 4: lot/batch traceability

def test_lot_required_when_product_is_lot_tracked(eg):
    serum = Product.objects.create(company=eg.c, sku="RM-SERUM", name="Active serum", category=eg.fifo,
                                   track_lots=True)
    with pytest.raises(BusinessError, match="lot"):
        stock.receive(eg.c, serum, eg.wh, 10, 50, eg.d(1, 1), eg.cfo, counter_account=eg.A["320102"],
                      move_type=MoveType.OPENING)
    m = stock.receive(eg.c, serum, eg.wh, 10, 50, eg.d(1, 1), eg.cfo, counter_account=eg.A["320102"],
                      move_type=MoveType.OPENING, lot_number="SERUM-0001")
    assert m.lot.lot_number == "SERUM-0001" and m.lot.product_id == serum.pk
    assert Lot.objects.filter(company=eg.c, product=serum, lot_number="SERUM-0001").exists()

    with pytest.raises(BusinessError, match="lot"):
        stock.issue(eg.c, serum, eg.wh, 5, eg.d(1, 2), eg.cfo, counter_account=eg.A["510101"])


def test_fifo_issue_restricted_to_a_single_lot(eg):
    bottle = Product.objects.create(company=eg.c, sku="RM-BOTTLE", name="Glass bottle", category=eg.fifo,
                                    track_lots=True)
    stock.receive(eg.c, bottle, eg.wh, 100, 2, eg.d(1, 1), eg.cfo, counter_account=eg.A["320102"],
                 move_type=MoveType.OPENING, lot_number="LOT-A")
    stock.receive(eg.c, bottle, eg.wh, 100, 3, eg.d(1, 2), eg.cfo, counter_account=eg.A["320102"],
                 move_type=MoveType.OPENING, lot_number="LOT-B")
    # 150 units total are on hand, but LOT-A only has 100 -- asking for 150 from LOT-A specifically
    # must fail even though the warehouse as a whole has enough stock.
    with pytest.raises(BusinessError, match="layers_short|short"):
        stock.issue(eg.c, bottle, eg.wh, 150, eg.d(1, 3), eg.cfo, counter_account=eg.A["510101"],
                   lot_number="LOT-A")
    m = stock.issue(eg.c, bottle, eg.wh, 60, eg.d(1, 3), eg.cfo, counter_account=eg.A["510101"], lot_number="LOT-A")
    assert m.lot.lot_number == "LOT-A" and m.value == D(120)  # 60 × 2, not blended with LOT-B's cost


def test_manufacturing_lot_genealogy_traces_raw_batch_into_finished_batch(eg):
    steel = Product.objects.create(company=eg.c, sku="RM-STEEL2", name="Steel sheet", category=eg.fifo,
                                   track_lots=True)
    cabinet = Product.objects.create(company=eg.c, sku="FG-CAB2", name="Cabinet", category=eg.fifo,
                                     track_lots=True)
    stock.receive(eg.c, steel, eg.wh, 100, 200, eg.d(1, 1), eg.cfo, counter_account=eg.A["320102"],
                 move_type=MoveType.OPENING, lot_number="STEEL-BATCH-7")
    bom = Bom.objects.create(company=eg.c, code="BOM-CAB2", product=cabinet, quantity=1)
    BomComponent.objects.create(bom=bom, product=steel, quantity=D(2))
    mo = ManufacturingOrder.objects.create(company=eg.c, product=cabinet, bom=bom, quantity=D(10), warehouse=eg.wh,
                                           planned_date=eg.d(1, 5))
    mrp.confirm(mo, eg.cfo)
    comp = mo.components.get(product=steel)
    mrp.consume(mo, eg.cfo, on_date=eg.d(1, 5), lots={comp.pk: "STEEL-BATCH-7"})
    mrp.produce(mo, eg.cfo, 10, on_date=eg.d(1, 6), finish=True, lot_number="CAB-BATCH-1")

    raw_lot = Lot.objects.get(company=eg.c, product=steel, lot_number="STEEL-BATCH-7")
    fg_lot = Lot.objects.get(company=eg.c, product=cabinet, lot_number="CAB-BATCH-1")

    forward = stock.lot_genealogy(raw_lot)
    assert any(row["lot_number"] == "CAB-BATCH-1" for row in forward["forward_traceability"])

    backward = stock.lot_genealogy(fg_lot)
    assert any(row["lot_number"] == "STEEL-BATCH-7" for row in backward["backward_traceability"])


def test_lot_genealogy_api_endpoint(eg):
    steel = Product.objects.create(company=eg.c, sku="RM-STEEL3", name="Steel sheet", category=eg.fifo,
                                   track_lots=True)
    stock.receive(eg.c, steel, eg.wh, 50, 200, eg.d(1, 1), eg.cfo, counter_account=eg.A["320102"],
                 move_type=MoveType.OPENING, lot_number="STEEL-BATCH-9")
    lot = Lot.objects.get(company=eg.c, product=steel, lot_number="STEEL-BATCH-9")

    cfo = client(eg, eg.cfo)
    r = cfo.get(f"/api/lots/{lot.pk}/genealogy/")
    assert r.status_code == 200
    assert r.data["lot"]["lot_number"] == "STEEL-BATCH-9"
