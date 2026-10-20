"""Month-end revaluation of foreign-currency monetary items (EAS 13 / IAS 21).

Open customer/vendor documents and foreign-currency bank/cash accounts are restated at the closing
rate. The difference is booked to *unrealised* FX gain/loss and automatically reversed the next day,
so realised FX on settlement keeps being computed against the original document rate.
"""
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import Sum

from apps.core import audit
from apps.core.exceptions import BusinessError
from apps.core.models import ExchangeRate
from apps.core.money import ZERO
from apps.ledger import services as gl
from apps.ledger.models import Account, JournalEntry, JournalLine

from .models import Allocation, DocState, Invoice, InvoiceType

SOURCE = "fx_revaluation"


def open_amount_at(inv, on_date):
    paid = (Allocation.objects.filter(invoice=inv, date__lte=on_date).aggregate(s=Sum("amount"))["s"] or ZERO) + \
           (Allocation.objects.filter(credit_note=inv, date__lte=on_date).aggregate(s=Sum("amount"))["s"] or ZERO)
    return inv.amount_total - paid


def preview(company, on_date):
    base = company.base_currency_id
    items = defaultdict(list)
    for inv in (Invoice.objects.filter(company=company, state=DocState.POSTED, date__lte=on_date)
                .exclude(currency_id=base).select_related("partner")):
        open_amt = open_amount_at(inv, on_date)
        if open_amt <= 0:
            continue
        rate = ExchangeRate.rate_for(company, inv.currency_id, on_date)
        diff = gl.to_base(company, open_amt, rate) - gl.to_base(company, open_amt, inv.rate)
        if not diff:
            continue
        debit_side = inv.move_type in (InvoiceType.OUT_INVOICE, InvoiceType.IN_REFUND)
        from .services import control_account
        items[inv.currency_id].append({"kind": "document", "number": inv.number, "partner": inv.partner,
                                       "account": control_account(inv), "open": open_amt, "rate": rate,
                                       "booked_rate": inv.rate, "correction": diff if debit_side else -diff})
    for acc in Account.objects.filter(company=company, currency__isnull=False, account_type__in=["bank", "cash"]) \
            .exclude(currency_id=base):
        agg = JournalLine.objects.filter(company=company, account=acc, posted=True, date__lte=on_date).aggregate(
            fc=Sum("amount_currency"), d=Sum("debit"), c=Sum("credit"))
        fc, book = agg["fc"] or ZERO, (agg["d"] or ZERO) - (agg["c"] or ZERO)
        if not fc and not book:
            continue
        rate = ExchangeRate.rate_for(company, acc.currency_id, on_date)
        diff = gl.to_base(company, fc, rate) - book
        if diff:
            items[acc.currency_id].append({"kind": "account", "number": acc.code, "partner": None, "account": acc,
                                           "open": fc, "rate": rate, "booked_rate": None, "correction": diff})
    return items


@transaction.atomic
def revalue(company, on_date, user):
    if JournalEntry.objects.filter(company=company, source_type=SOURCE, date=on_date, reversal_of__isnull=True,
                                   state="posted").exists():
        raise BusinessError(f"Foreign-currency balances were already revalued on {on_date}.", "already_revalued")
    st = gl.settings_for(company)
    gain_acc = gl.require_account(st.unrealized_fx_gain_account, "unrealised FX gain")
    loss_acc = gl.require_account(st.unrealized_fx_loss_account, "unrealised FX loss")
    journal = gl.journal_for(company, "general_journal", "general")
    entries = []
    for cur, items in preview(company, on_date).items():
        grouped = defaultdict(lambda: ZERO)  # (account, partner) -> correction (+ = debit)
        for it in items:
            grouped[(it["account"], it["partner"])] += it["correction"]
        specs, net = [], ZERO
        for (acc, partner), amt in grouped.items():
            if not amt:
                continue
            net += amt
            specs.append(gl.LineSpec(account=acc, debit=amt if amt > 0 else ZERO, credit=-amt if amt < 0 else ZERO,
                                     partner=partner, amount_currency=ZERO, description=f"FX revaluation {cur}"))
        if not specs:
            continue
        # net > 0: assets went up / liabilities went down -> gain
        specs.append(gl.LineSpec(account=gain_acc if net > 0 else loss_acc, debit=-net if net < 0 else ZERO,
                                 credit=net if net > 0 else ZERO, amount_currency=ZERO,
                                 description=f"Unrealised FX {'gain' if net > 0 else 'loss'} {cur}"))
        rate = items[0]["rate"]
        e = gl.create_entry(company, journal, on_date, specs, user=user, reference=f"FX REVAL {cur} {on_date}",
                            narration=f"Revaluation of {cur} balances at {rate} (auto-reversed next day)",
                            currency=cur, rate=rate, source=(SOURCE, 0))
        gl.post_entry(e, user, allow_control=True)
        gl.reverse_entry(e, user, on_date + timedelta(days=1), "Automatic reversal of FX revaluation",
                         allow_source=True)
        entries.append(e)
    audit.record("fx_revaluation", entries[0] if entries else None, {"date": on_date,
                                                                     "entries": [e.number for e in entries]},
                 company_id=company.pk, object_type="ledger.journalentry")
    return entries
