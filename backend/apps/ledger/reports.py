"""Financial reports. All figures in company base currency, from POSTED lines only."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Q, Sum

from apps.core.money import ZERO

from .models import DEBIT_NORMAL, PL_CLASSES, Account, AccountClass, JournalLine


def _sums(company, *, date_from=None, date_to=None, before=None, exclude_closing=False, extra=None):
    qs = JournalLine.objects.filter(company=company, posted=True)
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    if before:
        qs = qs.filter(date__lt=before)
    if exclude_closing:
        qs = qs.filter(entry__is_closing=False)
    if extra:
        qs = qs.filter(extra)
    return {r["account"]: (r["d"] or ZERO, r["c"] or ZERO)
            for r in qs.values("account").annotate(d=Sum("debit"), c=Sum("credit"))}


def _tree(company):
    accounts = list(Account.objects.filter(company=company).order_by("code"))
    children = defaultdict(list)
    for a in accounts:
        children[a.parent_id].append(a)
    return accounts, children


def _rollup(accounts, children, leaf_values, keys):
    """Sum leaf values up the tree. leaf_values: {account_id: {key: Decimal}}"""
    totals = {}

    def visit(acc):
        vals = {k: leaf_values.get(acc.pk, {}).get(k, ZERO) for k in keys}
        for ch in children.get(acc.pk, []):
            cv = visit(ch)
            for k in keys:
                vals[k] += cv[k]
        totals[acc.pk] = vals
        return vals

    for root in children.get(None, []):
        visit(root)
    return totals


def trial_balance(company, date_from, date_to, *, max_level=None, hide_zero=True):
    """Opening balance (before date_from), period debit/credit, closing balance — per account,
    rolled up the account tree. Once prior years are closed, P&L opening balances are zero
    because the closing entry moved them to retained earnings."""
    opening = _sums(company, before=date_from)
    movement = _sums(company, date_from=date_from, date_to=date_to)
    accounts, children = _tree(company)
    leaf = {}
    for a in accounts:
        od, oc = opening.get(a.pk, (ZERO, ZERO))
        md, mc = movement.get(a.pk, (ZERO, ZERO))
        leaf[a.pk] = {"opening": od - oc, "debit": md, "credit": mc, "closing": od - oc + md - mc}
    keys = ["opening", "debit", "credit", "closing"]
    totals = _rollup(accounts, children, leaf, keys)
    rows = []
    for a in accounts:
        t = totals.get(a.pk)
        if t is None:
            continue
        if max_level and a.level > max_level:
            continue
        if hide_zero and not any(t[k] for k in keys):
            continue
        rows.append({"account_id": a.pk, "code": a.code, "name": a.name, "level": a.level,
                     "is_group": a.is_group, "account_class": a.account_class,
                     **{k: str(t[k]) for k in keys}})
    roots = children.get(None, [])
    grand = {k: sum((totals[r.pk][k] for r in roots), ZERO) for k in keys}
    return {"date_from": date_from, "date_to": date_to, "rows": rows,
            "totals": {k: str(v) for k, v in grand.items()},
            "balanced": grand["debit"] == grand["credit"] and grand["opening"] == 0 and grand["closing"] == 0}


def general_ledger(company, account, date_from, date_to, partner=None):
    ids = account.descendant_ids()
    flt = Q(account_id__in=ids)
    if partner:
        flt &= Q(partner=partner)
    o = _sums(company, before=date_from, extra=flt)
    opening = sum((d - c for d, c in o.values()), ZERO)
    qs = (JournalLine.objects.filter(company=company, posted=True, date__gte=date_from, date__lte=date_to)
          .filter(flt).select_related("entry", "account", "partner", "entry__journal")
          .order_by("date", "entry__number", "sequence"))
    running, rows = opening, []
    for ln in qs:
        running += ln.debit - ln.credit
        rows.append({"date": ln.date, "entry_id": ln.entry_id, "number": ln.entry.number,
                     "journal": ln.entry.journal.code, "account": ln.account.code,
                     "partner": ln.partner.name if ln.partner else "", "description": ln.description or ln.entry.narration,
                     "reference": ln.entry.reference, "debit": str(ln.debit), "credit": str(ln.credit),
                     "currency": ln.currency_id, "amount_currency": str(ln.amount_currency), "balance": str(running)})
    return {"account": {"id": account.pk, "code": account.code, "name": account.name},
            "opening_balance": str(opening), "closing_balance": str(running), "rows": rows}


def _statement(company, classes, *, date_from=None, date_to=None, max_level=None, exclude_closing=False):
    sums = _sums(company, date_from=date_from, date_to=date_to, exclude_closing=exclude_closing)
    accounts, children = _tree(company)
    leaf = {a.pk: {"bal": sums.get(a.pk, (ZERO, ZERO))[0] - sums.get(a.pk, (ZERO, ZERO))[1]} for a in accounts}
    totals = _rollup(accounts, children, leaf, ["bal"])
    sections = []
    for cls in classes:
        sign = Decimal(1) if cls in DEBIT_NORMAL else Decimal(-1)
        rows, section_total = [], ZERO
        for a in accounts:
            if a.account_class != cls or (max_level and a.level > max_level):
                continue
            v = totals[a.pk]["bal"] * sign
            if a.level == 1:
                section_total += v
            if v:
                rows.append({"account_id": a.pk, "code": a.code, "name": a.name, "level": a.level,
                             "is_group": a.is_group, "amount": str(v)})
        sections.append({"class": cls, "label": AccountClass(cls).label, "rows": rows, "total": section_total})
    return sections


def balance_sheet(company, as_of, max_level=None):
    sections = _statement(company, [AccountClass.ASSET, AccountClass.LIABILITY, AccountClass.EQUITY],
                          date_to=as_of, max_level=max_level)
    # Unclosed profit/loss to date belongs in equity until year-end closing moves it.
    pl = _sums(company, date_to=as_of, extra=Q(account__account_class__in=PL_CLASSES))
    current_result = -sum((d - c for d, c in pl.values()), ZERO)  # positive = profit
    assets, liab, equity = (s["total"] for s in sections)
    equity_total = equity + current_result
    out = []
    for s in sections:
        s["total"] = str(s["total"])
        out.append(s)
    return {"as_of": as_of, "sections": out, "current_year_result": str(current_result),
            "total_assets": str(assets), "total_liabilities": str(liab), "total_equity": str(equity_total),
            "total_liabilities_and_equity": str(liab + equity_total),
            "balanced": assets == liab + equity_total}


def income_statement(company, date_from, date_to, max_level=None):
    sections = _statement(company, [AccountClass.INCOME, AccountClass.EXPENSE], date_from=date_from,
                          date_to=date_to, max_level=max_level, exclude_closing=True)
    income, expense = sections[0]["total"], sections[1]["total"]
    cogs_sum = _sums(company, date_from=date_from, date_to=date_to, exclude_closing=True,
                     extra=Q(account__account_type="cost_of_sales"))
    cogs = sum((d - c for d, c in cogs_sum.values()), ZERO)
    rev_sum = _sums(company, date_from=date_from, date_to=date_to, exclude_closing=True,
                    extra=Q(account__account_type="revenue"))
    revenue = -sum((d - c for d, c in rev_sum.values()), ZERO)
    for s in sections:
        s["total"] = str(s["total"])
    return {"date_from": date_from, "date_to": date_to, "sections": sections,
            "revenue": str(revenue), "cost_of_sales": str(cogs), "gross_profit": str(revenue - cogs),
            "total_income": str(income), "total_expense": str(expense), "net_profit": str(income - expense)}
