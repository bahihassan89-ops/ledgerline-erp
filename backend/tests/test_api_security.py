import pyotp
import pytest
from rest_framework.test import APIClient

from apps.core.models import Company
from apps.integrations.models import ApiKey
from apps.ledger.models import Account

from .conftest import PWD, D


def je_payload(env, amount=100):
    return {"journal": env.J["GEN"].pk, "date": str(env.d(3, 1)), "narration": "t",
            "lines": [{"account": env.A["520201"].pk, "debit": str(amount), "credit": "0"},
                      {"account": env.A["110110"].pk, "debit": "0", "credit": str(amount)}]}


def test_clerk_can_draft_but_not_post(env, api):
    cl = api(env.clerk)
    r = cl.post("/api/journal-entries/", je_payload(env), format="json")
    assert r.status_code == 201, r.content
    r2 = cl.post(f"/api/journal-entries/{r.data['id']}/post/")
    assert r2.status_code == 403
    r3 = api(env.accountant).post(f"/api/journal-entries/{r.data['id']}/post/")
    assert r3.status_code == 200 and r3.data["state"] == "posted"


def test_unbalanced_api_entry_rejected(env, api):
    p = je_payload(env)
    p["lines"][1]["credit"] = "99"
    r = api(env.clerk).post("/api/journal-entries/", p, format="json")
    assert r.status_code == 400


def test_auditor_read_only_and_admin_has_no_accounting(env, api):
    from .conftest import make_user
    aud = make_user(env.c, "aud", "Auditor (read-only)")
    assert api(aud).get("/api/accounts/").status_code == 200
    assert api(aud).post("/api/journal-entries/", je_payload(env), format="json").status_code == 403
    assert api(env.admin).get("/api/journal-entries/").status_code == 403


def test_account_type_filter_accepts_lowercase_values(env, api):
    """Regression test (item #31): AccountType used to be built via models.TextChoices("AccountType",
    [(k, label) for k in ACCOUNT_TYPES]) -- the Enum *functional* API, which reads 2-tuples as
    (member_name, member_value), not (value, label). That silently produced Title-Case choice
    *values* ("Group") instead of the lowercase keys ("group") every account actually stores and
    every caller filters by (see apps/ledger/services.py, dashboard.py, reports.py, views.py, all
    of which filter account_type="group"/"receivable"/etc. in lowercase). django-filter validates
    ?account_type=<value> against Account._meta.get_field("account_type").choices, so a lowercase
    query parameter was rejected with 400 -- which is exactly what the parent-account dropdown in
    the frontend's Accounts.tsx sends (GET /accounts/?account_type=group), leaving it empty on
    every load, not just intermittently."""
    r = api(env.accountant).get("/api/accounts/", {"account_type": "group"})
    assert r.status_code == 200, r.content
    results = r.data["results"] if isinstance(r.data, dict) else r.data
    assert len(results) > 0
    assert all(a["account_type"] == "group" for a in results)
    # "5202" (used as a group account elsewhere in this test suite) is actually in the result.
    assert "5202" in {a["code"] for a in results}
    assert api(env.admin).get("/api/users/").status_code == 200


def test_cross_company_isolation(env, api):
    other = Company.objects.create(name="Other", code="other", base_currency_id="USD")
    cl = api(env.accountant)
    cl.credentials(HTTP_X_COMPANY=str(other.pk))
    assert cl.get("/api/accounts/").status_code == 403
    # foreign-key smuggling: referencing another company's account
    from apps.core.management.commands.setup_company import setup
    setup(other, D(0), env.d(1, 1).year)
    foreign = Account.objects.filter(company=other, account_type="expense").first()
    p = je_payload(env)
    p["lines"][0]["account"] = foreign.pk
    r = api(env.clerk).post("/api/journal-entries/", p, format="json")
    assert r.status_code == 400 and "another company" in str(r.data)


def test_cannot_escalate_privileges(env, api):
    from apps.iam.models import Role
    cfo_role = Role.objects.get(company=env.c, name="CFO / Finance Director")
    r = api(env.admin).patch(f"/api/users/{env.clerk.pk}/", {"role_ids": [cfo_role.pk]}, format="json")
    assert r.status_code == 400
    r = api(env.admin).post("/api/roles/", {"name": "x", "level": 1, "permissions": ["ledger.entry.post"]},
                            format="json")
    assert r.status_code == 400


def test_login_lockout_and_mfa(env, settings):
    settings.ERP_MFA_REQUIRED_LEVEL = 1  # pin the policy explicitly -- the dev/test default disables it
    cl = APIClient()
    for _ in range(settings.ERP_MAX_FAILED_LOGINS):
        assert cl.post("/api/auth/login/", {"username": "clerk", "password": "wrong"}).status_code == 401
    assert cl.post("/api/auth/login/", {"username": "clerk", "password": PWD}).status_code == 423
    from django.core.cache import cache
    cache.clear()  # reset the login rate limiter (10/min) for the MFA part

    # Controller (level 4) must enrol MFA before getting tokens
    r = cl.post("/api/auth/login/", {"username": "controller", "password": PWD})
    assert r.data.get("mfa_setup_required") and "access" not in r.data
    tok = r.data["mfa_token"]
    secret = cl.post("/api/auth/mfa/setup/", {"mfa_token": tok}).data["secret"]
    code = pyotp.TOTP(secret).now()
    r = cl.post("/api/auth/mfa/confirm/", {"mfa_token": tok, "code": code})
    assert r.status_code == 200 and "access" in r.data
    # next login requires a code; replaying the same code is refused
    r = cl.post("/api/auth/login/", {"username": "controller", "password": PWD})
    assert r.data.get("mfa_required")
    assert cl.post("/api/auth/mfa/verify/", {"mfa_token": r.data["mfa_token"], "code": code}).status_code == 401


def test_jwt_flow_and_refresh_rotation(env, settings):
    settings.ERP_MFA_REQUIRED_LEVEL = 1  # pin the policy explicitly -- the dev/test default disables it
    cl = APIClient()
    r = cl.post("/api/auth/login/", {"username": "accountant", "password": PWD})
    # Every role now requires MFA (ERP_MFA_REQUIRED_LEVEL=1), so a first login walks through enrolment.
    assert r.data.get("mfa_setup_required") and "access" not in r.data
    tok = r.data["mfa_token"]
    secret = cl.post("/api/auth/mfa/setup/", {"mfa_token": tok}).data["secret"]
    code = pyotp.TOTP(secret).now()
    r = cl.post("/api/auth/mfa/confirm/", {"mfa_token": tok, "code": code})
    assert "access" in r.data
    cl.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
    me = cl.get("/api/auth/me/")
    assert me.status_code == 200 and "ledger.entry.post" in me.data["companies"][0]["permissions"]
    r2 = APIClient().post("/api/auth/refresh/", {"refresh": r.data["refresh"]})
    assert r2.status_code == 200
    assert APIClient().post("/api/auth/refresh/", {"refresh": r.data["refresh"]}).status_code == 401  # reused


def test_api_key_scopes_are_capped_by_owner(env):
    key, raw = ApiKey.generate(company=env.c, name="crm", owner=env.clerk,
                               scopes=["partners.partner.view", "ledger.entry.post"])
    cl = APIClient()
    cl.credentials(HTTP_AUTHORIZATION=f"Api-Key {raw}")
    assert cl.get("/api/partners/").status_code == 200
    assert cl.get("/api/accounts/").status_code == 403  # not in scopes
    cl.credentials(HTTP_AUTHORIZATION="Api-Key " + raw[:-2] + "xx")
    assert cl.get("/api/partners/").status_code == 401


def test_reports_endpoints(env, api):
    cl = api(env.accountant)
    for name in ["trial-balance", "balance-sheet", "income-statement", "ar-aging", "ap-aging", "vat",
                 "subledger-reconciliation", "inventory-valuation"]:
        r = cl.get(f"/api/reports/{name}/")
        assert r.status_code == 200, (name, r.content)
    assert cl.get("/api/dashboard/").status_code == 200
    assert cl.get("/api/accounts/tree/").status_code == 200


def test_team_crud_requires_team_manage(env, api):
    from apps.iam.models import Team
    # Controller holds iam.team.manage (see permissions_catalog.CONTROLLER) and can staff a team.
    r = api(env.controller).post("/api/teams/", {"name": "Inventory Team", "department": "inventory"},
                                 format="json")
    assert r.status_code == 201, r.content
    team_id = r.data["id"]
    # A plain Accountant has no iam.team.manage and is refused, both to create and to edit.
    assert api(env.accountant).post("/api/teams/", {"name": "x", "department": "other"},
                                    format="json").status_code == 403
    assert api(env.accountant).patch(f"/api/teams/{team_id}/", {"name": "y"}, format="json").status_code == 403
    # Everyone holding iam.user.view can at least list teams (narrower than editing them).
    assert api(env.accountant).get("/api/teams/").status_code == 200
    assert Team.objects.filter(pk=team_id, company=env.c, name="Inventory Team").exists()


def test_team_rejects_cross_company_member(env, api):
    from .conftest import make_user
    other = Company.objects.create(name="Other2", code="other2", base_currency_id="USD")
    from apps.core.management.commands.setup_company import setup
    setup(other, D(0), env.d(1, 1).year)
    foreign_user = make_user(other, "foreign", "Accounting Clerk")
    foreign_membership = foreign_user.membership(other.pk)
    r = api(env.controller).post("/api/teams/", {"name": "Bad Team", "department": "inventory",
                                                 "member_ids": [foreign_membership.pk]}, format="json")
    assert r.status_code == 400


def test_inventory_and_procurement_department_roles_scoped(env, api):
    from .conftest import make_user
    inv_mgr = make_user(env.c, "inv_mgr", "Inventory Manager")
    inv_off = make_user(env.c, "inv_off", "Inventory Officer")
    proc_mgr = make_user(env.c, "proc_mgr", "Procurement Manager")
    proc_off = make_user(env.c, "proc_off", "Procurement Officer")

    stock_payload = {"product": env.fifo_prod.pk, "warehouse": env.wh.pk, "quantity": "10",
                     "unit_cost": "5", "counter_account": env.A["410101"].pk}
    # Inventory Manager holds inventory.stock.adjust -> can book opening stock.
    r = api(inv_mgr).post("/api/stock-moves/opening/", stock_payload, format="json")
    assert r.status_code == 201, r.content
    # Inventory Officer (day-to-day only) does not hold inventory.stock.adjust.
    assert api(inv_off).post("/api/stock-moves/opening/", stock_payload, format="json").status_code == 403
    # Procurement Manager/Officer have no inventory.stock.* rights at all.
    assert api(proc_mgr).post("/api/stock-moves/opening/", stock_payload, format="json").status_code == 403
    assert api(proc_off).post("/api/stock-moves/opening/", stock_payload, format="json").status_code == 403

    po_payload = {"partner": env.vendor.pk, "date": str(env.d(3, 1)), "currency": env.c.base_currency_id,
                 "warehouse": env.wh.pk,
                 "lines": [{"product": env.fifo_prod.pk, "quantity": "1", "unit_price": "10"}]}
    # Procurement Officer can raise a PO (purchasing.order.create) but not confirm it.
    r = api(proc_off).post("/api/purchase-orders/", po_payload, format="json")
    assert r.status_code == 201, r.content
    assert api(proc_off).post(f"/api/purchase-orders/{r.data['id']}/confirm/").status_code == 403
    # Procurement Manager can confirm it.
    assert api(proc_mgr).post(f"/api/purchase-orders/{r.data['id']}/confirm/").status_code == 200
    # Inventory roles have no purchasing rights at all.
    assert api(inv_mgr).post("/api/purchase-orders/", po_payload, format="json").status_code == 403


def test_field_permission_mixin_hides_and_protects_gl_fields(env, api):
    from .conftest import make_user
    inv_mgr = make_user(env.c, "fp_inv_mgr", "Inventory Manager")
    income_acc = env.A["410101"]
    expense_acc = env.A["520201"]
    env.fifo_prod.income_account = income_acc
    env.fifo_prod.expense_account = expense_acc
    env.fifo_prod.sale_tax = env.vat_s
    env.fifo_prod.save(update_fields=["income_account", "expense_account", "sale_tax"])

    # An Inventory Manager (inventory.product.manage, but no ledger.account.manage/ledger.tax.manage)
    # never sees the GL-mapping fields in a read...
    r = api(inv_mgr).get(f"/api/products/{env.fifo_prod.pk}/")
    assert r.status_code == 200
    for f in ["income_account", "expense_account", "sale_tax", "purchase_tax"]:
        assert f not in r.data
    # ...the ordinary catalogue fields they do own are unaffected...
    assert r.data["sku"] == env.fifo_prod.sku

    # ...and cannot set/change one of those fields even though they can otherwise edit the product.
    r = api(inv_mgr).patch(f"/api/products/{env.fifo_prod.pk}/",
                          {"reorder_level": "5", "expense_account": income_acc.pk}, format="json")
    assert r.status_code == 400
    assert "expense_account" in r.data

    # A same-valued (unchanged) echo of the protected field is silently dropped, not rejected.
    r = api(inv_mgr).patch(f"/api/products/{env.fifo_prod.pk}/",
                          {"reorder_level": "6", "expense_account": expense_acc.pk}, format="json")
    assert r.status_code == 200, r.content
    env.fifo_prod.refresh_from_db()
    assert env.fifo_prod.reorder_level == D(6) and env.fifo_prod.expense_account_id == expense_acc.pk

    # The Controller, who does hold ledger.account.manage/ledger.tax.manage, sees and can edit them.
    r = api(env.controller).get(f"/api/products/{env.fifo_prod.pk}/")
    assert r.data["income_account"] == income_acc.pk and r.data["sale_tax"] == env.vat_s.pk
    r = api(env.controller).patch(f"/api/products/{env.fifo_prod.pk}/", {"expense_account": income_acc.pk},
                                  format="json")
    assert r.status_code == 200, r.content


def test_manufacturing_consume_produce_requires_inventory_stock_move(env, api):
    """consume/produce physically move stock (raw-material issue / finished-goods receipt), so --
    like PurchaseOrderViewSet.receive and SalesOrderViewSet.deliver -- they're gated by
    inventory.stock.move, not the weaker manufacturing.order.manage (item #30: align the
    stock-move permission check across every route that actually moves stock, not just
    StockMoveViewSet's own actions)."""
    from apps.inventory import services as stock
    from apps.inventory.models import Product
    from apps.manufacturing.models import Bom, BomComponent
    from .conftest import make_user

    steel = Product.objects.create(company=env.c, sku="RM-STEEL", name="Steel", category=env.fifo)
    stock.receive(env.c, steel, env.wh, 100, 10, env.d(1, 1), env.cfo, counter_account=env.A["320102"])
    bom = Bom.objects.create(company=env.c, code="BOM-FIFO", product=env.fifo_prod, quantity=1)
    BomComponent.objects.create(bom=bom, product=steel, quantity=2)

    cl = api(env.controller)
    r = cl.post("/api/manufacturing-orders/", {"product": env.fifo_prod.pk, "bom": bom.pk, "quantity": "5",
                                               "warehouse": env.wh.pk, "planned_date": str(env.d(1, 5))},
               format="json")
    assert r.status_code == 201, r.content
    mo_id = r.data["id"]
    assert cl.post(f"/api/manufacturing-orders/{mo_id}/confirm/").status_code == 200

    from apps.iam.models import Role
    planner_only = Role.objects.create(company=env.c, name="MO Planner Only", level=2,
                                       permissions=["manufacturing.order.view", "manufacturing.order.manage"])
    mover_only = Role.objects.create(company=env.c, name="Stock Mover Only", level=1,
                                     permissions=["inventory.stock.view", "inventory.stock.move"])
    planner = make_user(env.c, "mo_planner", "Accounting Clerk")
    planner.membership(env.c.pk).roles.set([planner_only])
    mover = make_user(env.c, "stock_mover", "Accounting Clerk")
    mover.membership(env.c.pk).roles.set([mover_only])

    # manufacturing.order.manage alone is no longer enough to actually issue/receive stock.
    assert api(planner).post(f"/api/manufacturing-orders/{mo_id}/consume/").status_code == 403
    # inventory.stock.move alone -- with no manufacturing.order.manage at all -- is sufficient,
    # mirroring receive/deliver's existing convention that the inventory holder, not the order
    # planner, is who physically moves the stock.
    assert api(mover).post(f"/api/manufacturing-orders/{mo_id}/consume/").status_code == 200
    r = api(mover).post(f"/api/manufacturing-orders/{mo_id}/produce/", {"quantity": "5", "finish": True},
                        format="json")
    assert r.status_code == 200, r.content
    assert api(planner).post(f"/api/manufacturing-orders/{mo_id}/produce/", {"quantity": "5"},
                             format="json").status_code == 403


def test_idempotency_key_prevents_duplicates(env, api):
    from apps.ledger.models import JournalEntry
    cl = api(env.clerk)
    r1 = cl.post("/api/journal-entries/", je_payload(env), format="json", HTTP_IDEMPOTENCY_KEY="abc-1")
    r2 = cl.post("/api/journal-entries/", je_payload(env), format="json", HTTP_IDEMPOTENCY_KEY="abc-1")
    assert r1.status_code == r2.status_code == 201
    assert r1.data["id"] == r2.data["id"] and r2.headers.get("Idempotent-Replay") == "true"
    assert JournalEntry.objects.filter(company=env.c).count() == 1
