from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from apps.assets.services import add_months
from apps.core import audit
from apps.core.exceptions import BusinessError
from apps.core.models import CostCenter
from apps.core.money import ZERO, D
from apps.ledger import services as gl
from apps.ledger.models import DEBIT_NORMAL, Account, FiscalPeriod, JournalLine
from apps.partners.models import Partner

from .models import Budget, BudgetLine, RecurringEntry

STEP = {"monthly": 1, "quarterly": 3, "yearly": 12}


@transaction.atomic
def set_budget_lines(budget, rows):
    """rows: [{account, cost_center, period, amount}] — replaces all lines (draft budgets only)."""
    if budget.state != "draft":
        raise BusinessError("Approved budgets are frozen; copy to a new version to change them.", "approved")
    periods = {p.pk: p for p in FiscalPeriod.objects.filter(fiscal_year=budget.fiscal_year)}
    budget.lines.all().delete()
    objs = []
    for r in rows:
        acc = Account.objects.get(pk=r["account"], company=budget.company)
        if acc.is_group:
            raise BusinessError(f"{acc.code} is a group account; budget at postable level.", "group_account")
        if int(r["period"]) not in periods:
            raise BusinessError("Period is outside the budget's fiscal year.", "invalid_period")
        cc = CostCenter.objects.get(pk=r["cost_center"], company=budget.company) if r.get("cost_center") else None
        if D(r.get("amount")):
            objs.append(BudgetLine(budget=budget, account=acc, cost_center=cc, period_id=int(r["period"]),
                                   amount=D(r["amount"])))
    BudgetLine.objects.bulk_create(objs)
    audit.record("set_lines", budget, {"lines": len(objs)})
    return len(objs)


@transaction.atomic
def approve_budget(budget, user):
    budget = Budget.objects.select_for_update().get(pk=budget.pk)
    if budget.state != "draft":
        raise BusinessError("Only draft budgets can be approved.", "not_draft")
    if not budget.lines.exists():
        raise BusinessError("Add budget lines before approving.", "no_lines")
    budget.state = "approved"
    budget.save(update_fields=["state", "updated_at"])
    audit.record("approve", budget, {"lines": budget.lines.count()}, repr_=budget.name)
    return budget


def budget_vs_actual(budget, date_from, date_to, cost_center=None):
    company = budget.company
    lines = budget.lines.filter(period__start_date__gte=date_from, period__end_date__lte=date_to)
    if cost_center:
        lines = lines.filter(cost_center=cost_center)
    bud = defaultdict(lambda: ZERO)
    for b in lines.values("account").annotate(s=Sum("amount")):
        bud[b["account"]] = b["s"]
    act_qs = JournalLine.objects.filter(company=company, posted=True, date__gte=date_from, date__lte=date_to,
                                        entry__is_closing=False)
    if cost_center:
        act_qs = act_qs.filter(cost_center=cost_center)
    act_qs = act_qs.filter(account_id__in=set(bud) | set(
        Account.objects.filter(company=company, account_class__in=["income", "expense"]).values_list("id", flat=True)))
    acts = {a["account"]: (a["d"] or ZERO) - (a["c"] or ZERO)
            for a in act_qs.values("account").annotate(d=Sum("debit"), c=Sum("credit"))}
    rows = []
    for acc in Account.objects.filter(pk__in=set(bud) | set(acts)).order_by("code"):
        sign = Decimal(1) if acc.account_class in DEBIT_NORMAL else Decimal(-1)
        actual = acts.get(acc.pk, ZERO) * sign
        b = bud.get(acc.pk, ZERO)
        variance = actual - b
        income_like = acc.account_class in ("income", "liability", "equity")
        rows.append({"account_id": acc.pk, "code": acc.code, "name": acc.name, "account_class": acc.account_class,
                     "budget": str(b), "actual": str(actual), "variance": str(variance),
                     "variance_pct": str(round(variance / b * 100, 1)) if b else None,
                     "favourable": (variance >= 0) if income_like else (variance <= 0)})
    tot = lambda cls, k: sum((D(r[k]) for r in rows if r["account_class"] == cls), ZERO)
    return {"budget": budget.name, "date_from": date_from, "date_to": date_to, "rows": rows,
            "income": {"budget": str(tot("income", "budget")), "actual": str(tot("income", "actual"))},
            "expense": {"budget": str(tot("expense", "budget")), "actual": str(tot("expense", "actual"))}}


def _specs(rec):
    out = []
    for l in rec.lines:
        acc = Account.objects.get(pk=l["account"], company=rec.company)
        out.append(gl.LineSpec(
            account=acc, debit=D(l.get("debit")), credit=D(l.get("credit")), description=l.get("description", ""),
            partner=Partner.objects.get(pk=l["partner"], company=rec.company) if l.get("partner") else None,
            cost_center=CostCenter.objects.get(pk=l["cost_center"], company=rec.company) if l.get("cost_center") else None))
    return out


@transaction.atomic
def run_due(company, today, user=None):
    """Create (and optionally post) every recurring entry due on or before `today`."""
    created = []
    for rec in RecurringEntry.objects.select_for_update().filter(company=company, active=True, next_date__lte=today):
        actor = user or rec.created_by
        while rec.next_date <= today and (rec.end_date is None or rec.next_date <= rec.end_date):
            entry = gl.create_entry(company, rec.journal, rec.next_date, _specs(rec), user=actor,
                                    reference=f"REC {rec.name}"[:120], narration=rec.narration or rec.name)
            if rec.auto_reverse_days:
                entry.auto_reverse_date = rec.next_date + timedelta(days=rec.auto_reverse_days)
                entry.save(update_fields=["auto_reverse_date"])
            if rec.auto_post and actor and "ledger.entry.post" in actor.permissions_for(company.pk):
                gl.post_entry(entry, actor)
            rec.last_entry = entry
            created.append(entry)
            rec.next_date = add_months(rec.next_date, STEP[rec.frequency]).replace(
                day=min(rec.next_date.day, add_months(rec.next_date, STEP[rec.frequency]).day))
        if rec.end_date and rec.next_date > rec.end_date:
            rec.active = False
        rec.save()
    if created:
        audit.record("recurring_run", created[-1], {"created": len(created)})
    return created
