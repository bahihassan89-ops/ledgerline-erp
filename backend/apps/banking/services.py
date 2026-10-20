"""Bank reconciliation: import statements, match them to GL bank lines, and prove
  balance per bank − outstanding payments + outstanding receipts = balance per books."""
import csv
import io
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q, Sum

from apps.core import audit
from apps.core.exceptions import BusinessError
from apps.core.money import ZERO, D
from apps.ledger import services as gl
from apps.ledger.models import JournalLine

from .models import BankStatement, BankStatementLine

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d.%m.%Y"]


def _date(s):
    s = (s or "").strip()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    raise BusinessError(f"Unrecognised date '{s}'. Use YYYY-MM-DD or DD/MM/YYYY.", "csv_date")


def _amount(s):
    s = (s or "").strip().replace(",", "").replace(" ", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    if not s:
        return ZERO
    try:
        return Decimal(s)
    except InvalidOperation:
        raise BusinessError(f"Unrecognised amount '{s}'.", "csv_amount")


@transaction.atomic
def import_csv(company, journal, text, opening_balance, closing_balance, user, reference=""):
    """CSV columns (header row, case-insensitive): date, description, reference, and either `amount`
    (signed) or `debit`/`credit` (bank's view: credit = money in). Also accepts deposit/withdrawal."""
    if journal.company_id != company.pk or journal.type != "bank":
        raise BusinessError("Choose a bank journal of this company.", "invalid_journal")
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        raise BusinessError("The file has no data rows.", "csv_empty")
    norm = [{(k or "").strip().lower(): (v or "") for k, v in r.items()} for r in rows]
    lines = []
    for i, r in enumerate(norm, start=2):
        if not any(v.strip() for v in r.values()):
            continue
        if "amount" in r:
            amt = _amount(r["amount"])
        elif "deposit" in r or "withdrawal" in r:
            amt = _amount(r.get("deposit")) - _amount(r.get("withdrawal"))
        elif "credit" in r or "debit" in r:
            amt = _amount(r.get("credit")) - _amount(r.get("debit"))
        else:
            raise BusinessError("CSV needs an 'amount' column or 'debit'/'credit' columns.", "csv_columns")
        lines.append(BankStatementLine(date=_date(r.get("date") or r.get("value date")),
                                       description=(r.get("description") or r.get("details") or "")[:255],
                                       reference=(r.get("reference") or r.get("ref") or "")[:120], amount=amt))
    total = sum((l.amount for l in lines), ZERO)
    opening, closing = D(opening_balance), D(closing_balance)
    if opening + total != closing:
        raise BusinessError(f"Statement does not add up: opening {opening} + movements {total} ≠ closing {closing}.",
                            "statement_unbalanced")
    st = BankStatement.objects.create(company=company, journal=journal, reference=reference,
                                      date_from=min(l.date for l in lines), date_to=max(l.date for l in lines),
                                      opening_balance=opening, closing_balance=closing, created_by=user)
    for l in lines:
        l.statement = st
    BankStatementLine.objects.bulk_create(lines)
    audit.record("bank_statement_import", st, {"lines": len(lines), "closing": closing})
    return st


def _candidates(company, account, amount, around, days=10):
    qs = JournalLine.objects.filter(company=company, account=account, posted=True, bank_match__isnull=True,
                                    date__gte=around - timedelta(days=days), date__lte=around + timedelta(days=days))
    return qs.filter(debit=amount) if amount > 0 else qs.filter(credit=-amount)


@transaction.atomic
def auto_match(statement):
    """Match by exact amount within ±10 days; prefer a line whose reference appears in the statement text."""
    account = statement.journal.default_account
    n = 0
    for sl in statement.lines.filter(matched_line__isnull=True):
        cands = list(_candidates(statement.company, account, sl.amount, sl.date).select_related("entry"))
        if not cands:
            continue
        text = f"{sl.description} {sl.reference}".lower()
        best = [c for c in cands if c.entry.reference and any(tok and tok.lower() in text
                                                              for tok in c.entry.reference.split())]
        pick = best[0] if len(best) == 1 else (cands[0] if len(cands) == 1 else None)
        if pick:
            sl.matched_line, sl.matched_by = pick, "auto"
            sl.save(update_fields=["matched_line", "matched_by"])
            n += 1
    return n


@transaction.atomic
def match(sl, journal_line):
    account = sl.statement.journal.default_account
    if journal_line.account_id != account.pk or not journal_line.posted:
        raise BusinessError("Choose a posted line on this bank account.", "invalid_line")
    if (journal_line.debit - journal_line.credit) != sl.amount:
        raise BusinessError("Amounts differ; split the item or post an adjusting entry.", "amount_mismatch")
    if hasattr(journal_line, "bank_match") and journal_line.bank_match.pk != sl.pk:
        raise BusinessError("That GL line is already matched to another statement line.", "already_matched")
    sl.matched_line, sl.matched_by = journal_line, "manual"
    sl.save(update_fields=["matched_line", "matched_by"])
    return sl


@transaction.atomic
def unmatch(sl):
    if sl.statement.state == "reconciled":
        raise BusinessError("Statement is reconciled; reopen it first.", "reconciled")
    sl.matched_line, sl.matched_by = None, ""
    sl.save(update_fields=["matched_line", "matched_by"])
    return sl


@transaction.atomic
def create_entry_for_line(sl, counter_account, user, description=""):
    """Bank charges, interest, direct debits that are only known from the statement."""
    if sl.matched_line_id:
        raise BusinessError("Line is already matched.", "already_matched")
    if counter_account.reconcilable and not sl.partner:
        raise BusinessError("Set the partner on the statement line to post to a receivable/payable account.",
                            "partner_required")
    st = sl.statement
    bank = st.journal.default_account
    amt = abs(sl.amount)
    desc = description or sl.description or "Bank statement item"
    specs = [gl.LineSpec(account=bank, debit=amt if sl.amount > 0 else ZERO, credit=amt if sl.amount < 0 else ZERO,
                         description=desc),
             gl.LineSpec(account=counter_account, debit=amt if sl.amount < 0 else ZERO,
                         credit=amt if sl.amount > 0 else ZERO, partner=sl.partner, description=desc)]
    e = gl.create_entry(st.company, st.journal, sl.date, specs, user=user, reference=sl.reference or "Bank statement",
                        narration=desc, source=("bank_statement", st.pk))
    gl.post_entry(e, user, allow_control=counter_account.reconcilable)
    bank_line = e.lines.get(account=bank)
    sl.matched_line, sl.matched_by = bank_line, "created"
    sl.save(update_fields=["matched_line", "matched_by"])
    return e


@transaction.atomic
def close_statement(statement):
    open_lines = statement.lines.filter(matched_line__isnull=True).count()
    if open_lines:
        raise BusinessError(f"{open_lines} statement lines are not matched yet.", "unmatched_lines")
    statement.state = "reconciled"
    statement.save(update_fields=["state", "updated_at"])
    audit.record("bank_reconciled", statement, {"closing": statement.closing_balance})
    return statement


def reconciliation_report(company, journal, as_of):
    """Classic bank reconciliation statement."""
    account = journal.default_account
    last = (BankStatement.objects.filter(company=company, journal=journal, date_to__lte=as_of)
            .order_by("-date_to", "-id").first())
    bank_balance = last.closing_balance if last else ZERO
    books = JournalLine.objects.filter(company=company, account=account, posted=True, date__lte=as_of)
    agg = books.aggregate(d=Sum("debit"), c=Sum("credit"))
    book_balance = (agg["d"] or ZERO) - (agg["c"] or ZERO)
    statement_end = last.date_to if last else None
    # Reconciliation starts at the first imported statement; its opening balance must equal the books then.
    first = BankStatement.objects.filter(company=company, journal=journal).order_by("date_from", "id").first()
    start = first.date_from if first else as_of
    pre = books.filter(date__lt=start).aggregate(d=Sum("debit"), c=Sum("credit"))
    book_at_start = (pre["d"] or ZERO) - (pre["c"] or ZERO)
    unmatched = books.filter(bank_match__isnull=True, date__gte=start).select_related("entry", "partner").order_by("date")
    receipts = [l for l in unmatched if l.debit > 0]
    payments = [l for l in unmatched if l.credit > 0]
    # statement lines not yet in the books
    stl = BankStatementLine.objects.filter(statement__company=company, statement__journal=journal,
                                           matched_line__isnull=True, date__lte=as_of)
    in_bank_not_books = sum((l.amount for l in stl), ZERO)
    rec_total = sum((l.debit for l in receipts), ZERO)
    pay_total = sum((l.credit for l in payments), ZERO)
    adjusted_bank = bank_balance + rec_total - pay_total
    adjusted_books = book_balance + in_bank_not_books

    def row(l):
        return {"date": l.date, "number": l.entry.number, "reference": l.entry.reference,
                "partner": l.partner.name if l.partner else "", "description": l.description or l.entry.narration,
                "amount": str(l.debit or l.credit)}
    return {"as_of": as_of, "journal": journal.code, "account": f"{account.code} {account.name}",
            "statement_date": statement_end, "balance_per_bank": str(bank_balance),
            "outstanding_receipts": [row(l) for l in receipts], "outstanding_receipts_total": str(rec_total),
            "outstanding_payments": [row(l) for l in payments], "outstanding_payments_total": str(pay_total),
            "adjusted_bank_balance": str(adjusted_bank), "balance_per_books": str(book_balance),
            "statement_items_not_in_books": str(in_bank_not_books), "adjusted_book_balance": str(adjusted_books),
            "difference": str(adjusted_bank - adjusted_books), "reconciled": adjusted_bank == adjusted_books,
            "reconciliation_start": start, "book_balance_at_start": str(book_at_start),
            "first_statement_opening": str(first.opening_balance) if first else None,
            "start_agrees": (first.opening_balance == book_at_start) if first else None}
