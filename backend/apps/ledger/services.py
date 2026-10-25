"""General-ledger posting engine.

Every financial event in the system (manual entry, invoice, payment, stock move) ends up as a
JournalEntry created and posted through this module. It is the single place where the
double-entry, period and account rules are enforced in Python (the DB triggers are the backstop).
"""
import calendar
from dataclasses import dataclass, field
from datetime import date as date_cls
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core import audit
from apps.core.exceptions import BusinessError
from apps.core.models import Branch, ExchangeRate, Sequence
from apps.core.money import ZERO, D, round_to

from .models import (PL_CLASSES, Account, AccountingSettings, EntryState, FiscalPeriod, FiscalYear, Journal,
                     JournalEntry, JournalLine, PeriodState)


@dataclass
class LineSpec:
    """One journal line. `debit`/`credit` are in company BASE currency.
    `amount_currency` is the signed amount in the entry currency (defaults to debit - credit)."""
    account: Account
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    amount_currency: Decimal | None = None
    partner: object = None
    description: str = ""
    cost_center: object = None
    tax: object = None
    tax_base: Decimal = ZERO
    product: object = None
    quantity: Decimal = ZERO
    due_date: date_cls | None = None
    extra: dict = field(default_factory=dict)


def default_branch(company) -> Branch:
    """Every entry needs a branch; callers that don't operate multiple branches don't have to
    think about it. Raises if setup_company hasn't run (every company is seeded with exactly
    one `is_main` branch -- see Branch.Meta's one_main_branch_per_company constraint)."""
    branch = Branch.objects.filter(company=company, is_main=True).first()
    if not branch:
        raise BusinessError("Company has no main branch. Run setup_company first.", "no_main_branch")
    return branch


def base_decimals(company) -> int:
    return company.base_currency.decimals


def to_base(company, amount, rate) -> Decimal:
    return round_to(D(amount) * D(rate), base_decimals(company))


def resolve_rate(company, currency_code, on_date, rate=None) -> Decimal:
    if currency_code == company.base_currency_id:
        return Decimal("1")
    if rate:
        rate = D(rate)
        if rate <= 0:
            raise BusinessError("Exchange rate must be positive.", "invalid_rate")
        return rate
    return ExchangeRate.rate_for(company, currency_code, on_date)


def lines_from_document_amounts(company, rate, rows):
    """Convert rows of (LineSpec with debit/credit in DOCUMENT currency) into base currency,
    keeping amount_currency, and push any conversion rounding difference onto the largest line
    so the entry balances exactly in base currency."""
    doc_diff = sum((D(s.debit) - D(s.credit) for s in rows), ZERO)
    if doc_diff:
        raise BusinessError(f"Entry is not balanced in document currency (difference {doc_diff}).", "unbalanced")
    out = []
    for spec in rows:
        doc_amount = D(spec.debit) - D(spec.credit)
        base = to_base(company, abs(doc_amount), rate)
        spec.amount_currency = doc_amount
        spec.debit, spec.credit = (base, ZERO) if doc_amount > 0 else (ZERO, base)
        out.append(spec)
    diff = sum((s.debit - s.credit for s in out), ZERO)
    if diff:
        # Put the rounding cent on the side that has more lines (it accumulated the rounding),
        # so a single bank/control line keeps its exact converted amount.
        debits = [s for s in out if s.debit]
        credits = [s for s in out if s.credit]
        side = debits if len(debits) >= len(credits) else credits
        biggest = max(side, key=lambda s: s.debit + s.credit)
        if biggest.debit:
            biggest.debit -= diff
        else:
            biggest.credit += diff
    return out


# --------------------------------------------------------------------------- periods

def find_period(company, on_date) -> FiscalPeriod:
    period = (FiscalPeriod.objects.select_related("fiscal_year")
              .filter(company=company, start_date__lte=on_date, end_date__gte=on_date)
              .order_by("number").first())
    if not period:
        raise BusinessError(f"No fiscal period covers {on_date}. Create the fiscal year first.", "no_period")
    return period


def check_posting_date(company, on_date, user, *, closing=False) -> FiscalPeriod:
    if company.lock_date and on_date <= company.lock_date:
        raise BusinessError(f"Books are locked up to {company.lock_date}.", "lock_date")
    period = find_period(company, on_date)
    if period.state == PeriodState.LOCKED:
        raise BusinessError(f"Period {period.name} is locked.", "period_locked")
    if period.fiscal_year.closed and not closing:
        raise BusinessError(f"Fiscal year {period.fiscal_year.name} is closed.", "year_closed")
    if period.state == PeriodState.CLOSED and not closing:
        if "ledger.period.post_closed" not in user.permissions_for(company.pk):
            raise BusinessError(f"Period {period.name} is closed. Only authorised users may post into it.",
                                "period_closed")
    return period


def create_fiscal_year(company, start_year: int, user=None) -> FiscalYear:
    m = company.fiscal_year_start_month
    start = date_cls(start_year, m, 1)
    end_month = (m - 2) % 12 + 1
    end_year = start_year if m == 1 else start_year + 1
    end = date_cls(end_year, end_month, calendar.monthrange(end_year, end_month)[1])
    if FiscalYear.objects.filter(company=company, start_date__lte=end, end_date__gte=start).exists():
        raise BusinessError("A fiscal year overlapping these dates already exists.", "fy_overlap")
    name = str(start_year) if m == 1 else f"{start_year}/{end_year % 100:02d}"
    fy = FiscalYear.objects.create(company=company, name=name, start_date=start, end_date=end, created_by=user)
    y, mo = start_year, m
    for n in range(1, 13):
        last = calendar.monthrange(y, mo)[1]
        FiscalPeriod.objects.create(company=company, fiscal_year=fy, number=n,
                                    name=f"{calendar.month_abbr[mo]} {y}", start_date=date_cls(y, mo, 1),
                                    end_date=date_cls(y, mo, last), created_by=user)
        mo += 1
        if mo > 12:
            mo, y = 1, y + 1
    audit.record("create_fiscal_year", fy)
    return fy


def set_period_state(period, state, user):
    perms = user.permissions_for(period.company_id)
    if period.state == PeriodState.LOCKED:
        raise BusinessError("A locked period cannot be reopened.", "period_locked")
    need = "ledger.period.lock" if state == PeriodState.LOCKED else "ledger.period.close"
    if need not in perms:
        raise BusinessError(f"Missing permission: {need}", "forbidden")
    if state == PeriodState.LOCKED:
        earlier_open = FiscalPeriod.objects.filter(company_id=period.company_id, start_date__lt=period.start_date,
                                                   state=PeriodState.OPEN).exists()
        if earlier_open:
            raise BusinessError("Close all earlier periods before locking this one.", "earlier_open")
    if state in (PeriodState.CLOSED, PeriodState.LOCKED):
        drafts = JournalEntry.objects.filter(company_id=period.company_id, state=EntryState.DRAFT,
                                             date__gte=period.start_date, date__lte=period.end_date).count()
        if drafts and state == PeriodState.LOCKED:
            raise BusinessError(f"{drafts} draft entries are dated in this period. Post or delete them first.",
                                "drafts_in_period")
    old = period.state
    period.state = state
    period.save(update_fields=["state", "updated_at"])
    audit.record("period_state", period, {"state": [old, state]})
    return period


# --------------------------------------------------------------------------- entries

@transaction.atomic
def create_entry(company, journal, on_date, lines, *, user=None, reference="", narration="",
                 currency=None, rate=None, source=None, is_closing=False, branch=None) -> JournalEntry:
    if journal.company_id != company.pk:
        raise BusinessError("Journal belongs to another company.", "cross_company")
    branch = branch or default_branch(company)
    if branch.company_id != company.pk:
        raise BusinessError("Branch belongs to another company.", "cross_company")
    currency_code = getattr(currency, "code", currency) or company.base_currency_id
    rate = resolve_rate(company, currency_code, on_date, rate)
    entry = JournalEntry.objects.create(
        company=company, journal=journal, branch=branch, date=on_date, reference=reference[:120],
        narration=narration, currency_id=currency_code, rate=rate, created_by=user, is_closing=is_closing,
        source_type=source[0] if source else "", source_id=source[1] if source else None,
    )
    _write_lines(entry, lines)
    return entry


def _write_lines(entry, lines):
    company = entry.company
    objs = []
    for i, s in enumerate(lines, start=1):
        # Normalise to one non-negative side (a negative debit is a credit and vice versa).
        net = round_to(D(s.debit) - D(s.credit), base_decimals(company))
        debit, credit = (net, ZERO) if net > 0 else (ZERO, -net)
        if not net:
            continue  # zero lines carry no information
        amount_currency = s.amount_currency if s.amount_currency is not None else debit - credit
        objs.append(JournalLine(
            entry=entry, company=company, sequence=i, account=s.account, partner=s.partner,
            description=(s.description or "")[:255], debit=debit, credit=credit, currency_id=entry.currency_id,
            amount_currency=amount_currency, cost_center=s.cost_center, tax=s.tax, tax_base=D(s.tax_base),
            product=s.product, quantity=D(s.quantity), due_date=s.due_date, date=entry.date,
            account_type=s.account.account_type, branch_id=entry.branch_id,
        ))
    JournalLine.objects.bulk_create(objs)


@transaction.atomic
def replace_lines(entry, lines):
    if entry.state != EntryState.DRAFT:
        raise BusinessError("Only draft entries can be edited.", "not_draft")
    entry.lines.all().delete()
    _write_lines(entry, lines)


@transaction.atomic
def post_entry(entry, user, *, allow_control=False, closing=False) -> JournalEntry:
    entry = JournalEntry.objects.select_for_update().select_related("journal", "company").get(pk=entry.pk)
    company = entry.company
    if entry.state not in (EntryState.DRAFT, EntryState.APPROVED):
        raise BusinessError(f"Entry {entry} cannot be posted from state '{entry.state}'.", "not_draft")
    if not entry.journal.active:
        raise BusinessError("Journal is inactive.", "journal_inactive")
    if entry.is_manual and not entry.journal.allow_manual and not closing:
        raise BusinessError(f"Journal {entry.journal.code} does not accept manual entries.", "journal_not_manual")

    lines = list(entry.lines.select_related("account"))
    errors = []
    if len(lines) < 2:
        errors.append("An entry needs at least two lines.")
    total_debit = total_credit = ZERO
    for ln in lines:
        acc = ln.account
        tag = f"Line {ln.sequence} ({acc.code})"
        if acc.company_id != company.pk:
            errors.append(f"{tag}: account belongs to another company.")
        if acc.is_group:
            errors.append(f"{tag}: '{acc.name}' is a group account; post to one of its sub-accounts.")
        if not acc.active:
            errors.append(f"{tag}: account is inactive.")
        if acc.is_control and entry.is_manual and not allow_control:
            errors.append(f"{tag}: '{acc.name}' is a control account; use invoices/payments/stock operations.")
        if acc.reconcilable and not ln.partner_id:
            errors.append(f"{tag}: a customer/vendor is required on receivable/payable accounts.")
        if acc.requires_cost_center and not ln.cost_center_id:
            errors.append(f"{tag}: a cost center is required for this account.")
        if acc.currency_id and acc.currency_id != entry.currency_id:
            errors.append(f"{tag}: account only accepts {acc.currency_id}.")
        if ln.partner_id and ln.partner.company_id != company.pk:
            errors.append(f"{tag}: partner belongs to another company.")
        total_debit += ln.debit
        total_credit += ln.credit
    if total_debit != total_credit:
        errors.append(f"Entry is not balanced: debits {total_debit} ≠ credits {total_credit} "
                      f"(difference {total_debit - total_credit}).")
    if total_debit == 0:
        errors.append("Entry total is zero.")
    if errors:
        raise BusinessError("The entry cannot be posted.", "invalid_entry", {"errors": errors})

    period = check_posting_date(company, entry.date, user, closing=closing)
    fy = period.fiscal_year
    entry.number = Sequence.next_number(company, f"journal:{entry.journal.code}", fy.start_date.year,
                                        entry.journal.code)
    entry.lines.update(posted=True, date=entry.date)
    entry.period = period
    entry.total_debit = total_debit
    entry.total_credit = total_credit
    entry.state = EntryState.POSTED
    entry.posted_by = user
    entry.posted_at = timezone.now()
    entry.save()
    audit.record("post", entry, {"number": entry.number, "total": str(total_debit), "journal": entry.journal.code})
    from apps.integrations.events import emit
    emit(company, "journal_entry.posted", {"id": entry.pk, "number": entry.number, "date": entry.date,
                                           "total": total_debit, "source_type": entry.source_type})
    if entry.auto_reverse_date and not entry.reversal_of_id:
        if entry.auto_reverse_date <= entry.date:
            raise BusinessError("The auto-reversal date must be after the entry date.", "invalid_reverse_date")
        reverse_entry(entry, user, entry.auto_reverse_date, f"Automatic reversal of accrual {entry.number}",
                      allow_source=True)
    return entry


@transaction.atomic
def reverse_entry(entry, user, on_date=None, reason="", *, allow_source=False) -> JournalEntry:
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.state != EntryState.POSTED:
        raise BusinessError("Only posted entries can be reversed.", "not_posted")
    if JournalEntry.objects.filter(reversal_of=entry).exists():
        raise BusinessError(f"Entry {entry.number} is already reversed.", "already_reversed")
    if entry.reversal_of_id:
        raise BusinessError("A reversal cannot itself be reversed; post a new entry instead.", "reversal_of_reversal")
    if entry.source_type and not allow_source:
        raise BusinessError(f"This entry was generated by a {entry.source_type}. Cancel or credit the "
                            f"source document instead.", "source_document")
    on_date = on_date or entry.date
    specs = [LineSpec(account=ln.account, debit=ln.credit, credit=ln.debit, amount_currency=-ln.amount_currency,
                      partner=ln.partner, description=ln.description, cost_center=ln.cost_center, tax=ln.tax,
                      tax_base=-ln.tax_base, product=ln.product, quantity=ln.quantity, due_date=ln.due_date)
             for ln in entry.lines.select_related("account", "partner")]
    rev = create_entry(entry.company, entry.journal, on_date, specs, user=user,
                       reference=f"Reversal of {entry.number}", narration=reason or f"Reversal of {entry.number}",
                       currency=entry.currency_id, rate=entry.rate,
                       source=(entry.source_type, entry.source_id) if entry.source_type else None)
    rev.reversal_of = entry
    rev.save(update_fields=["reversal_of"])
    rev = post_entry(rev, user, allow_control=True)
    audit.record("reverse", entry, {"reversal": rev.number, "reason": reason})
    from apps.integrations.events import emit
    emit(entry.company, "journal_entry.reversed", {"id": entry.pk, "number": entry.number, "reversal": rev.number})
    return rev


def settings_for(company) -> AccountingSettings:
    s, _ = AccountingSettings.objects.get_or_create(company=company)
    return s


def require_account(acc, label):
    if acc is None:
        raise BusinessError(f"No {label} account configured. Set it in Accounting Settings.", "missing_account")
    return acc


def journal_for(company, attr, label) -> Journal:
    j = getattr(settings_for(company), attr)
    if j is None:
        raise BusinessError(f"No {label} journal configured.", "missing_journal")
    return j


# --------------------------------------------------------------------------- year end

@transaction.atomic
def close_fiscal_year(fy, user) -> JournalEntry | None:
    company = fy.company
    if "ledger.year.close" not in user.permissions_for(company.pk):
        raise BusinessError("Missing permission: ledger.year.close", "forbidden")
    if fy.closed:
        raise BusinessError("Fiscal year already closed.", "year_closed")
    open_periods = fy.periods.filter(state=PeriodState.OPEN).count()
    if open_periods:
        raise BusinessError(f"{open_periods} periods are still open. Close all periods first.", "periods_open")
    drafts = JournalEntry.objects.filter(company=company, state=EntryState.DRAFT,
                                         date__gte=fy.start_date, date__lte=fy.end_date).count()
    if drafts:
        raise BusinessError(f"{drafts} draft entries exist in this year.", "drafts_in_year")
    st = settings_for(company)
    re_acc = require_account(st.retained_earnings_account, "retained earnings")
    journal = journal_for(company, "closing_journal", "closing")
    balances = (JournalLine.objects.filter(company=company, posted=True, date__gte=fy.start_date,
                                           date__lte=fy.end_date, account__account_class__in=PL_CLASSES)
                .values("account").annotate(d=Sum("debit"), c=Sum("credit")))
    specs, net = [], ZERO
    accounts = {a.pk: a for a in Account.objects.filter(pk__in=[b["account"] for b in balances])}
    for b in balances:
        bal = (b["d"] or ZERO) - (b["c"] or ZERO)
        if bal == 0:
            continue
        net += bal
        specs.append(LineSpec(account=accounts[b["account"]], debit=-bal if bal < 0 else ZERO,
                              credit=bal if bal > 0 else ZERO, description=f"Close {fy.name}"))
    entry = None
    if specs:
        # net > 0 means expenses exceeded income (a loss): debit retained earnings
        specs.append(LineSpec(account=re_acc, debit=net if net > 0 else ZERO, credit=-net if net < 0 else ZERO,
                              description=f"{'Loss' if net > 0 else 'Profit'} for {fy.name}"))
        entry = create_entry(company, journal, fy.end_date, specs, user=user, reference=f"Year-end close {fy.name}",
                             narration=f"Closing of income and expense accounts for {fy.name}", is_closing=True)
        post_entry(entry, user, allow_control=True, closing=True)
    fy.closed = True
    fy.closing_entry = entry
    fy.save(update_fields=["closed", "closing_entry", "updated_at"])
    audit.record("close_year", fy, {"net_result": str(-net), "entry": entry.number if entry else None})
    return entry
