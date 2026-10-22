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


# Hard cap on rows returned per call. Busy accounts (the main bank/cash account, a
# control account) can accumulate hundreds of thousands of lines; building full ORM
# objects (with joined entry/account/partner/journal) and formatting every Decimal for
# all of them in one response is CPU-bound in Python, not DB-bound -- at scale this is
# slow enough to blow past typical request timeouts rather than just "being slow". See
# `limit`/`offset` below: callers that need a busy account's full history must page
# through it instead of asking for everything in one request.
GL_MAX_LIMIT = 1000


def general_ledger(company, account, date_from, date_to, partner=None, limit=None, offset=0):
    ids = account.descendant_ids()
    flt = Q(account_id__in=ids)
    if partner:
        flt &= Q(partner=partner)
    o = _sums(company, before=date_from, extra=flt)
    opening = sum((d - c for d, c in o.values()), ZERO)

    base_qs = (JournalLine.objects.filter(company=company, posted=True, date__gte=date_from, date__lte=date_to)
              .filter(flt))
    offset = max(0, int(offset or 0))
    limit = min(int(limit), GL_MAX_LIMIT) if limit else GL_MAX_LIMIT
    total_count = base_qs.count()  # cheap: single indexed aggregate, no joins (see loadtest benchmarks)

    ordered = base_qs.order_by("date", "entry__number", "sequence")
    # Running balance must account for every matching line before this page, not just
    # those before date_from. Sum only the two numeric columns for the skipped rows
    # (no select_related/joins) -- far cheaper than materializing them as full objects.
    if offset:
        skipped = ordered.values("debit", "credit")[:offset].aggregate(d=Sum("debit"), c=Sum("credit"))
        opening += (skipped["d"] or ZERO) - (skipped["c"] or ZERO)

    qs = (ordered.select_related("entry", "account", "partner", "entry__journal")[offset:offset + limit])
    running, rows = opening, []
    for ln in qs:
        running += ln.debit - ln.credit
        rows.append({"date": ln.date, "entry_id": ln.entry_id, "number": ln.entry.number,
                     "journal": ln.entry.journal.code, "account": ln.account.code,
                     "partner": ln.partner.name if ln.partner else "", "description": ln.description or ln.entry.narration,
                     "reference": ln.entry.reference, "debit": str(ln.debit), "credit": str(ln.credit),
                     "currency": ln.currency_id, "amount_currency": str(ln.amount_currency), "balance": str(running)})
    return {"account": {"id": account.pk, "code": account.code, "name": account.name},
            # Balance carried into this page (the account's true opening balance when offset=0,
            # or the running balance after all skipped rows otherwise).
            "opening_balance": str(opening), "closing_balance": str(running),
            "rows": rows, "total_count": total_count, "limit": limit, "offset": offset,
            "has_more": offset + len(rows) < total_count}


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


CF_DEFAULT = {
    "cash": "cash", "bank": "cash",
    "fixed_asset": "investing", "non_current_asset": "investing",
    "non_current_liability": "financing", "equity": "financing",
    "retained_earnings": "financing", "accumulated_depreciation": "operating",
}
CF_LABELS = {
    "receivable": "(Increase) / decrease in trade receivables",
    "notes_receivable": "(Increase) / decrease in notes receivable",
    "inventory": "(Increase) / decrease in inventories", "wip": "(Increase) / decrease in work in progress",
    "prepayment": "(Increase) / decrease in prepayments", "current_asset": "(Increase) / decrease in other receivables",
    "tax_input": "(Increase) / decrease in input VAT", "wht_receivable": "(Increase) / decrease in taxes withheld by customers",
    "payable": "Increase / (decrease) in trade payables", "notes_payable": "Increase / (decrease) in notes payable",
    "grni": "Increase / (decrease) in goods received not invoiced",
    "current_liability": "Increase / (decrease) in accruals and other payables",
    "tax_output": "Increase / (decrease) in output VAT", "wht_payable": "Increase / (decrease) in withholding tax payable",
    "accumulated_depreciation": "Depreciation and amortisation",
    "fixed_asset": "Purchase / disposal of property, plant and equipment",
    "non_current_asset": "Purchase / disposal of intangible and other non-current assets",
    "non_current_liability": "Proceeds / (repayment) of long-term borrowings",
    "equity": "Capital contributed / (withdrawn)",
    "retained_earnings": "Dividends and other movements in retained earnings",
}


def cash_flow(company, date_from, date_to):
    """Indirect method (EAS 4 / IAS 7). Built from balance movements of every non-cash account, so it
    always reconciles to the actual change in cash. Closing entries are ignored (net profit is shown
    instead of its transfer to retained earnings), and go-live opening-balance entries are part of
    opening cash, not flows of the period."""
    from collections import defaultdict
    from .services import settings_for
    opening_journal = settings_for(company).opening_journal_id
    qs = (JournalLine.objects.filter(company=company, posted=True, date__gte=date_from, date__lte=date_to,
                                     entry__is_closing=False).exclude(entry__journal_id=opening_journal)
          .values("account__account_type", "account__account_class", "account__cash_flow_category", "account__code",
                  "account__name").annotate(d=Sum("debit"), c=Sum("credit")))
    net_profit = ZERO
    sections = defaultdict(lambda: defaultdict(lambda: ZERO))
    cash_change = ZERO
    for r in qs:
        move = (r["d"] or ZERO) - (r["c"] or ZERO)  # + = debit movement
        if r["account__account_class"] in PL_CLASSES:
            net_profit -= move
            continue
        cat = r["account__cash_flow_category"] or CF_DEFAULT.get(r["account__account_type"], "operating")
        if cat == "cash":
            cash_change += move
            continue
        if cat == "none":
            continue
        label = CF_LABELS.get(r["account__account_type"], r["account__name"])
        sections[cat][label] += -move  # a debit movement in a non-cash account is a use of cash
    def sec(name):
        rows = [{"label": k, "amount": str(v)} for k, v in sections[name].items() if v]
        return rows, sum(sections[name].values(), ZERO)
    op_rows, op_total = sec("operating")
    inv_rows, inv_total = sec("investing")
    fin_rows, fin_total = sec("financing")
    operating = net_profit + op_total
    computed = operating + inv_total + fin_total
    opening_cash = JournalLine.objects.filter(
        Q(date__lt=date_from) | Q(date__lte=date_to, entry__journal_id=opening_journal),
        company=company, posted=True, account__account_type__in=["cash", "bank"]).aggregate(
        d=Sum("debit"), c=Sum("credit"))
    oc = (opening_cash["d"] or ZERO) - (opening_cash["c"] or ZERO)
    return {"date_from": date_from, "date_to": date_to, "net_profit": str(net_profit),
            "operating": {"rows": op_rows, "total": str(operating)},
            "investing": {"rows": inv_rows, "total": str(inv_total)},
            "financing": {"rows": fin_rows, "total": str(fin_total)},
            "net_change": str(computed), "opening_cash": str(oc), "closing_cash": str(oc + cash_change),
            "actual_cash_change": str(cash_change), "reconciled": computed == cash_change}


def comparative(fn, company, current, previous, **kw):
    """Run a statement for two periods and attach `previous` amounts to every row (by account)."""
    cur, prev = fn(company, *current, **kw), fn(company, *previous, **kw)
    index = {}
    for s in prev.get("sections", []):
        for r in s["rows"]:
            index[r["account_id"]] = r["amount"]
    for s in cur.get("sections", []):
        for r in s["rows"]:
            r["previous"] = index.get(r["account_id"], "0")
    cur["previous"] = {k: v for k, v in prev.items() if k != "sections"}
    return cur
