import pytest
from django.db import DatabaseError, connection, transaction

from apps.core.exceptions import BusinessError
from apps.core.models import AuditLog
from apps.ledger import reports
from apps.ledger import services as gl
from apps.ledger.models import FiscalPeriod, JournalEntry, JournalLine, PeriodState

from .conftest import D


def entry(env, debit_acc, credit_acc, amount, on=None, user=None, journal="GEN", **kw):
    return gl.create_entry(env.c, env.J[journal], on or env.d(3, 15), [
        gl.LineSpec(account=env.A[debit_acc], debit=D(amount), **kw),
        gl.LineSpec(account=env.A[credit_acc], credit=D(amount)),
    ], user=user or env.accountant)


def test_balanced_entry_posts_with_gapless_numbers(env):
    e1 = gl.post_entry(entry(env, "520201", "110110", 100), env.accountant)
    e2 = gl.post_entry(entry(env, "520202", "110110", 50), env.accountant)
    assert e1.number.endswith("/00001") and e2.number.endswith("/00002")
    assert e1.state == "posted" and e1.total_debit == D(100)


def test_unbalanced_entry_rejected(env):
    e = gl.create_entry(env.c, env.J["GEN"], env.d(3, 1), [
        gl.LineSpec(account=env.A["520201"], debit=D(100)),
        gl.LineSpec(account=env.A["110110"], credit=D(90)),
    ], user=env.accountant)
    with pytest.raises(BusinessError) as ex:
        gl.post_entry(e, env.accountant)
    assert "not balanced" in str(ex.value.details)


def test_group_account_and_control_account_rejected(env):
    e = entry(env, "5202", "110110", 10)  # 5202 is a group
    with pytest.raises(BusinessError) as ex:
        gl.post_entry(e, env.accountant)
    assert "group account" in str(ex.value.details)
    e = entry(env, "110201", "410101", 10)  # AR control account in a manual entry
    with pytest.raises(BusinessError) as ex:
        gl.post_entry(e, env.accountant)
    assert "control account" in str(ex.value.details)


def test_closed_and_locked_periods(env):
    p = FiscalPeriod.objects.get(company=env.c, start_date=env.d(2, 1))
    gl.set_period_state(FiscalPeriod.objects.get(company=env.c, start_date=env.d(1, 1)), PeriodState.CLOSED,
                        env.controller)
    gl.set_period_state(p, PeriodState.CLOSED, env.controller)
    with pytest.raises(BusinessError, match="closed"):
        gl.post_entry(entry(env, "520201", "110110", 10, on=env.d(2, 10)), env.accountant)
    # Controller holds "post into closed period"
    gl.post_entry(entry(env, "520201", "110110", 10, on=env.d(2, 10)), env.controller)
    JournalEntry.objects.filter(state="draft").delete()  # the refused draft
    with pytest.raises(BusinessError):  # controller cannot lock
        gl.set_period_state(p, PeriodState.LOCKED, env.controller)
    gl.set_period_state(p, PeriodState.LOCKED, env.cfo)
    with pytest.raises(BusinessError, match="locked"):
        gl.post_entry(entry(env, "520201", "110110", 10, on=env.d(2, 11)), env.cfo)
    with pytest.raises(BusinessError, match="cannot be reopened"):
        gl.set_period_state(p, PeriodState.OPEN, env.cfo)


def test_company_lock_date(env):
    env.c.lock_date = env.d(3, 31)
    env.c.save()
    with pytest.raises(BusinessError, match="locked up to"):
        gl.post_entry(entry(env, "520201", "110110", 10, on=env.d(3, 31)), env.cfo)


def test_reversal(env):
    e = gl.post_entry(entry(env, "520201", "110110", 100), env.accountant)
    rev = gl.reverse_entry(e, env.senior, reason="duplicate")
    assert rev.reversal_of_id == e.pk and rev.state == "posted"
    tb = reports.trial_balance(env.c, env.d(1, 1), env.d(12, 31))
    assert all(D(r["closing"]) == 0 for r in tb["rows"] if r["code"] == "520201")
    with pytest.raises(BusinessError, match="already reversed"):
        gl.reverse_entry(e, env.senior)


def test_db_blocks_editing_posted_entries(env):
    e = gl.post_entry(entry(env, "520201", "110110", 100), env.accountant)
    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic():
            JournalEntry.objects.filter(pk=e.pk).update(narration="tampered")
    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic():
            JournalLine.objects.filter(entry=e).update(debit=D(1))
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            JournalEntry.objects.filter(pk=e.pk).delete()


def test_db_rejects_unbalanced_posted_entry_at_commit(env):
    e = entry(env, "520201", "110110", 100)
    with pytest.raises(DatabaseError, match="not balanced"):
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
            JournalLine.objects.filter(entry=e, credit__gt=0).update(credit=D(90))
            JournalLine.objects.filter(entry=e).update(posted=True)
            JournalEntry.objects.filter(pk=e.pk).update(state="posted", number="HACK/1", total_debit=100,
                                                        total_credit=90)


def test_audit_log_append_only_and_chain(env):
    gl.post_entry(entry(env, "520201", "110110", 100), env.accountant)
    ok, _ = AuditLog.verify_chain()
    assert ok
    with pytest.raises(DatabaseError, match="append-only"):
        with transaction.atomic():
            AuditLog.objects.all().update(action="x")


def test_reports_balance_and_year_close(env):
    gl.post_entry(entry(env, "110110", "310101", 1000), env.accountant)
    inv_rev = gl.create_entry(env.c, env.J["GEN"], env.d(4, 1), [
        gl.LineSpec(account=env.A["110101"], debit=D(500)),
        gl.LineSpec(account=env.A["410102"], credit=D(500)),
    ], user=env.accountant)
    gl.post_entry(inv_rev, env.accountant)
    gl.post_entry(entry(env, "520201", "110110", 200, on=env.d(4, 2)), env.accountant)
    bs = reports.balance_sheet(env.c, env.d(12, 31))
    assert bs["balanced"] and D(bs["current_year_result"]) == D(300)
    pl = reports.income_statement(env.c, env.d(1, 1), env.d(12, 31))
    assert D(pl["net_profit"]) == D(300)
    for p in FiscalPeriod.objects.filter(company=env.c):
        gl.set_period_state(p, PeriodState.CLOSED, env.cfo)
    fy = FiscalPeriod.objects.filter(company=env.c).first().fiscal_year
    closing = gl.close_fiscal_year(fy, env.cfo)
    assert closing.is_closing
    bs = reports.balance_sheet(env.c, env.d(12, 31))
    assert bs["balanced"] and D(bs["current_year_result"]) == 0
    re = next(r for s in bs["sections"] for r in s["rows"] if r["code"] == "320101")
    assert D(re["amount"]) == D(300)
    # P&L report still shows the year's real result (closing entry excluded)
    assert D(reports.income_statement(env.c, env.d(1, 1), env.d(12, 31))["net_profit"]) == D(300)


def test_foreign_currency_entry_balances_in_base(env):
    from apps.core.models import ExchangeRate
    ExchangeRate.objects.create(company=env.c, currency_id="EUR", date=env.d(1, 1), rate=D("1.0833333333"))
    specs = [gl.LineSpec(account=env.A["520201"], debit=D("33.33")),
             gl.LineSpec(account=env.A["520202"], debit=D("33.33")),
             gl.LineSpec(account=env.A["520203"], debit=D("33.34")),
             gl.LineSpec(account=env.A["110111"], credit=D("100"))]
    specs = gl.lines_from_document_amounts(env.c, D("1.0833333333"), specs)
    e = gl.create_entry(env.c, env.J["GEN"], env.d(3, 3), specs, user=env.accountant, currency="EUR",
                        rate=D("1.0833333333"))
    e = gl.post_entry(e, env.accountant)
    assert e.total_debit == e.total_credit == D("108.33")


def test_account_tree_rules(env):
    from django.core.exceptions import ValidationError
    from apps.ledger.models import Account
    bad = Account(company=env.c, code="9999", name="x", parent=env.A["5202"], account_class="expense",
                  account_type="expense")
    with pytest.raises(ValidationError, match="start with the parent code"):
        bad.clean()
    bad = Account(company=env.c, code="520299", name="x", parent=env.A["5202"], account_class="expense",
                  account_type="revenue")
    with pytest.raises(ValidationError):
        bad.clean()
    leaf_parent = Account(company=env.c, code="52020101", name="x", parent=env.A["520201"],
                          account_class="expense", account_type="expense")
    with pytest.raises(ValidationError, match="group"):
        leaf_parent.clean()
