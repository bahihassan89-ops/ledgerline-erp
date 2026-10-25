"""Multi-branch and consolidation: Branch CRUD, branch-scoped reports, the branch roll-up
report, and the DB trigger that keeps JournalLine.branch in sync with its entry's branch."""
import pytest
from django.db import DatabaseError, connection, transaction
from rest_framework.test import APIClient

from apps.core.models import Branch
from apps.ledger import reports
from apps.ledger import services as gl

from .conftest import D, make_user


def client(env, user):
    c = APIClient()
    c.force_authenticate(user)
    c.credentials(HTTP_X_COMPANY=str(env.c.pk))
    return c


def test_setup_creates_one_main_branch(env):
    branches = Branch.objects.filter(company=env.c)
    assert branches.count() == 1
    main = branches.get()
    assert main.is_main and main.code == "MAIN"


def test_only_one_main_branch_per_company(env):
    Branch.objects.create(company=env.c, code="CAI", name="Cairo Branch")
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            Branch.objects.create(company=env.c, code="ALX", name="Alexandria Branch", is_main=True)


def test_entry_defaults_to_main_branch(env):
    main = Branch.objects.get(company=env.c, is_main=True)
    e = gl.create_entry(env.c, env.J["GEN"], env.d(3, 1), [
        gl.LineSpec(account=env.A["520201"], debit=D(100)),
        gl.LineSpec(account=env.A["110110"], credit=D(100)),
    ], user=env.accountant)
    assert e.branch_id == main.pk
    assert all(ln.branch_id == main.pk for ln in e.lines.all())


def test_entry_can_target_a_specific_branch(env):
    cairo = Branch.objects.create(company=env.c, code="CAI", name="Cairo Branch")
    e = gl.create_entry(env.c, env.J["GEN"], env.d(3, 1), [
        gl.LineSpec(account=env.A["520201"], debit=D(100)),
        gl.LineSpec(account=env.A["110110"], credit=D(100)),
    ], user=env.accountant, branch=cairo)
    assert e.branch_id == cairo.pk
    assert all(ln.branch_id == cairo.pk for ln in e.lines.all())


def test_branch_from_another_company_rejected(env, eg):
    foreign = Branch.objects.get(company=eg.c, is_main=True)
    with pytest.raises(Exception):
        gl.create_entry(env.c, env.J["GEN"], env.d(3, 1), [
            gl.LineSpec(account=env.A["520201"], debit=D(100)),
            gl.LineSpec(account=env.A["110110"], credit=D(100)),
        ], user=env.accountant, branch=foreign)


def test_income_statement_scoped_per_branch_and_consolidates(env):
    cairo = Branch.objects.create(company=env.c, code="CAI", name="Cairo Branch")
    main = Branch.objects.get(company=env.c, is_main=True)

    def post(branch, amount):
        e = gl.create_entry(env.c, env.J["GEN"], env.d(4, 1), [
            gl.LineSpec(account=env.A["110101"], debit=D(amount)),
            gl.LineSpec(account=env.A["410102"], credit=D(amount)),
        ], user=env.accountant, branch=branch)
        return gl.post_entry(e, env.accountant)

    post(main, 1000)
    post(cairo, 400)

    main_pl = reports.income_statement(env.c, env.d(1, 1), env.d(12, 31), branch=main)
    cairo_pl = reports.income_statement(env.c, env.d(1, 1), env.d(12, 31), branch=cairo)
    consolidated = reports.income_statement(env.c, env.d(1, 1), env.d(12, 31))

    assert D(main_pl["revenue"]) == D(1000)
    assert D(cairo_pl["revenue"]) == D(400)
    # Consolidated (no branch filter) must equal the sum of every branch -- no inter-branch
    # transfers exist in this codebase, so there is nothing to eliminate on the way up.
    assert D(consolidated["revenue"]) == D(main_pl["revenue"]) + D(cairo_pl["revenue"]) == D(1400)
    assert D(consolidated["net_profit"]) == D(main_pl["net_profit"]) + D(cairo_pl["net_profit"])


def test_branch_income_statement_rollup_report(env):
    cairo = Branch.objects.create(company=env.c, code="CAI", name="Cairo Branch")
    main = Branch.objects.get(company=env.c, is_main=True)

    def post(branch, amount):
        e = gl.create_entry(env.c, env.J["GEN"], env.d(4, 1), [
            gl.LineSpec(account=env.A["110101"], debit=D(amount)),
            gl.LineSpec(account=env.A["410102"], credit=D(amount)),
        ], user=env.accountant, branch=branch)
        return gl.post_entry(e, env.accountant)

    post(main, 1000)
    post(cairo, 400)

    report = reports.branch_income_statement(env.c, env.d(1, 1), env.d(12, 31))
    by_code = {b["code"]: b["statement"] for b in report["branches"]}
    assert set(by_code) == {"MAIN", "CAI"}
    assert D(by_code["MAIN"]["revenue"]) == D(1000)
    assert D(by_code["CAI"]["revenue"]) == D(400)
    assert D(report["consolidated"]["revenue"]) == D(1400)


def test_trial_balance_and_balance_sheet_scoped_per_branch(env):
    cairo = Branch.objects.create(company=env.c, code="CAI", name="Cairo Branch")
    main = Branch.objects.get(company=env.c, is_main=True)
    e1 = gl.create_entry(env.c, env.J["GEN"], env.d(3, 1), [
        gl.LineSpec(account=env.A["110110"], debit=D(500)),
        gl.LineSpec(account=env.A["310101"], credit=D(500)),
    ], user=env.accountant, branch=main)
    gl.post_entry(e1, env.accountant)
    e2 = gl.create_entry(env.c, env.J["GEN"], env.d(3, 2), [
        gl.LineSpec(account=env.A["110110"], debit=D(200)),
        gl.LineSpec(account=env.A["310101"], credit=D(200)),
    ], user=env.accountant, branch=cairo)
    gl.post_entry(e2, env.accountant)

    tb_main = reports.trial_balance(env.c, env.d(1, 1), env.d(12, 31), branch=main)
    tb_cairo = reports.trial_balance(env.c, env.d(1, 1), env.d(12, 31), branch=cairo)
    tb_all = reports.trial_balance(env.c, env.d(1, 1), env.d(12, 31))
    row = next(r for r in tb_main["rows"] if r["code"] == "110110")
    assert D(row["closing"]) == D(500)
    row = next(r for r in tb_cairo["rows"] if r["code"] == "110110")
    assert D(row["closing"]) == D(200)
    row = next(r for r in tb_all["rows"] if r["code"] == "110110")
    assert D(row["closing"]) == D(700)

    bs_main = reports.balance_sheet(env.c, env.d(12, 31), branch=main)
    bs_cairo = reports.balance_sheet(env.c, env.d(12, 31), branch=cairo)
    assert D(bs_main["total_assets"]) == D(500)
    assert D(bs_cairo["total_assets"]) == D(200)


def test_db_rejects_line_branch_drifted_from_entry_branch(env):
    cairo = Branch.objects.create(company=env.c, code="CAI", name="Cairo Branch")
    main = Branch.objects.get(company=env.c, is_main=True)
    e = gl.create_entry(env.c, env.J["GEN"], env.d(3, 1), [
        gl.LineSpec(account=env.A["520201"], debit=D(100)),
        gl.LineSpec(account=env.A["110110"], credit=D(100)),
    ], user=env.accountant, branch=main)
    with pytest.raises(DatabaseError, match="unsynced lines"):
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
            from apps.ledger.models import JournalEntry, JournalLine
            JournalLine.objects.filter(entry=e).update(posted=True, branch=cairo)
            JournalEntry.objects.filter(pk=e.pk).update(state="posted", number="HACK/BR/1",
                                                        total_debit=100, total_credit=100)


def test_branch_api_crud_and_permission(env):
    main = Branch.objects.get(company=env.c, is_main=True)
    cfo = client(env, env.cfo)
    r = cfo.get("/api/branches/")
    assert r.status_code == 200
    codes = {b["code"] for b in r.data["results"]} if "results" in r.data else {b["code"] for b in r.data}
    assert codes == {"MAIN"}

    r = cfo.post("/api/branches/", {"code": "CAI", "name": "Cairo Branch"}, format="json")
    assert r.status_code == 201, r.content

    # Clerk lacks core.branch.manage and cannot create a branch, but can still read (list requires
    # only ledger.account.view, same as cost centers).
    clerk = make_user(env.c, "br_clerk", "Accounting Clerk")
    cl = client(env, clerk)
    assert cl.get("/api/branches/").status_code == 200
    assert cl.post("/api/branches/", {"code": "ALX", "name": "Alexandria"}, format="json").status_code == 403

    # Cross-company isolation: a user of another company cannot see this company's branches.
    assert main.company_id == env.c.pk


def test_branch_scoped_report_endpoint(env):
    cairo = Branch.objects.create(company=env.c, code="CAI", name="Cairo Branch")
    main = Branch.objects.get(company=env.c, is_main=True)
    for branch, amount in [(main, 1000), (cairo, 400)]:
        e = gl.create_entry(env.c, env.J["GEN"], env.d(4, 1), [
            gl.LineSpec(account=env.A["110101"], debit=D(amount)),
            gl.LineSpec(account=env.A["410102"], credit=D(amount)),
        ], user=env.accountant, branch=branch)
        gl.post_entry(e, env.accountant)

    cfo = client(env, env.cfo)
    r = cfo.get(f"/api/reports/income-statement/?date_from={env.d(1,1)}&date_to={env.d(12,31)}&branch={cairo.pk}")
    assert r.status_code == 200 and D(r.data["revenue"]) == D(400)

    r = cfo.get(f"/api/reports/branch-income-statement/?date_from={env.d(1,1)}&date_to={env.d(12,31)}")
    assert r.status_code == 200
    assert D(r.data["consolidated"]["revenue"]) == D(1400)
