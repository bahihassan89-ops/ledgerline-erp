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


def test_jwt_flow_and_refresh_rotation(env):
    cl = APIClient()
    r = cl.post("/api/auth/login/", {"username": "accountant", "password": PWD})
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


def test_idempotency_key_prevents_duplicates(env, api):
    from apps.ledger.models import JournalEntry
    cl = api(env.clerk)
    r1 = cl.post("/api/journal-entries/", je_payload(env), format="json", HTTP_IDEMPOTENCY_KEY="abc-1")
    r2 = cl.post("/api/journal-entries/", je_payload(env), format="json", HTTP_IDEMPOTENCY_KEY="abc-1")
    assert r1.status_code == r2.status_code == 201
    assert r1.data["id"] == r2.data["id"] and r2.headers.get("Idempotent-Replay") == "true"
    assert JournalEntry.objects.filter(company=env.c).count() == 1
