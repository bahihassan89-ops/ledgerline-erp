"""Go-live and bulk imports.

Order for a migration from another system (all on the go-live date, normally the last day before
the first period in this system):
  1. Opening trial balance       — every account EXCEPT receivables, payables and inventory
  2. Open customer/vendor items  — each unpaid invoice/bill, so aging and allocation work
  3. Opening stock               — per product, quantity × unit cost (Stock → Opening stock)
Each step offsets to *Opening Balance Equity*. When all three are in, that account must be zero —
the month-end checklist verifies it.
"""
from decimal import Decimal

from django.db import transaction

from apps.core.exceptions import BusinessError
from apps.core.models import CostCenter
from apps.core.money import ZERO, D

from . import services as gl
from .models import Account

EXCLUDED_TYPES = {"receivable": "open customer items", "payable": "open vendor items",
                  "inventory": "opening stock", "wip": "manufacturing orders"}


def _account(company, code):
    acc = Account.objects.filter(company=company, code=str(code).strip()).first()
    if not acc:
        raise BusinessError(f"Unknown account code '{code}'.", "unknown_account")
    if acc.is_group:
        raise BusinessError(f"{acc.code} is a group account; import at postable level.", "group_account")
    return acc


@transaction.atomic
def opening_trial_balance(company, on_date, rows, user):
    st = gl.settings_for(company)
    obe = gl.require_account(st.opening_equity_account, "opening balance equity")
    journal = st.opening_journal or gl.journal_for(company, "general_journal", "general")
    specs, errors = [], []
    for i, r in enumerate(rows, 1):
        try:
            acc = _account(company, r.get("account_code"))
        except BusinessError as e:
            errors.append(f"Row {i}: {e.message}")
            continue
        if acc.account_type in EXCLUDED_TYPES:
            errors.append(f"Row {i}: {acc.code} {acc.name} — import this through {EXCLUDED_TYPES[acc.account_type]}.")
            continue
        d, c = D(r.get("debit")), D(r.get("credit"))
        if d < 0 or c < 0:
            errors.append(f"Row {i}: negative amounts are not allowed.")
            continue
        if d or c:
            specs.append(gl.LineSpec(account=acc, debit=d, credit=c, description="Opening balance"))
    if errors:
        raise BusinessError("The opening balances were not imported.", "import_errors", {"errors": errors})
    if not specs:
        raise BusinessError("No amounts to import.", "empty_import")
    diff = sum((s.debit - s.credit for s in specs), ZERO)
    if diff:
        specs.append(gl.LineSpec(account=obe, debit=-diff if diff < 0 else ZERO, credit=diff if diff > 0 else ZERO,
                                 description="Opening balance offset (receivables/payables/stock follow)"))
    e = gl.create_entry(company, journal, on_date, specs, user=user, reference="Opening trial balance",
                        narration="Opening balances imported at go-live")
    gl.post_entry(e, user, allow_control=True)
    return {"entry": e.number, "lines": len(specs), "opening_equity_offset": str(diff)}


@transaction.atomic
def open_items(company, kind, rows, user):
    """rows: partner_code, number (legacy no.), date, due_date, currency, amount (negative = credit note), rate"""
    from apps.billing.models import Invoice, InvoiceLine
    from apps.billing.services import post_invoice
    from apps.partners.models import Partner
    st = gl.settings_for(company)
    obe = gl.require_account(st.opening_equity_account, "opening balance equity")
    created, errors = [], []
    for i, r in enumerate(rows, 1):
        p = Partner.objects.filter(company=company, code=str(r.get("partner_code", "")).strip()).first()
        if not p:
            errors.append(f"Row {i}: unknown partner '{r.get('partner_code')}'.")
            continue
        amt = D(r.get("amount"))
        if not amt:
            continue
        if kind == "receivable":
            mt = "out_invoice" if amt > 0 else "out_refund"
        else:
            mt = "in_invoice" if amt > 0 else "in_refund"
        try:
            from datetime import date
            inv = Invoice.objects.create(
                company=company, move_type=mt, partner=p, date=date.fromisoformat(r["date"]),
                due_date=date.fromisoformat(r["due_date"]) if r.get("due_date") else None,
                currency_id=(r.get("currency") or company.base_currency_id).upper(),
                rate=D(r.get("rate") or 1), reference=str(r.get("number", ""))[:120], is_opening=True,
                notes="Opening item imported at go-live", created_by=user)
            InvoiceLine.objects.create(invoice=inv, description=f"Opening balance {r.get('number', '')}"[:255],
                                       account=obe, quantity=1, unit_price=abs(amt))
            post_invoice(inv, user)
            created.append(inv.number)
        except (KeyError, ValueError) as e:
            errors.append(f"Row {i}: {e}")
        except BusinessError as e:
            errors.append(f"Row {i}: {e.message} {e.details.get('errors', '')}")
    if errors:
        raise BusinessError("Open items were not imported.", "import_errors", {"errors": errors})
    return {"created": created}


@transaction.atomic
def journal_csv(company, journal, on_date, narration, rows, user):
    """Draft journal entry from a spreadsheet (payroll summaries, allocations, bulk accruals).
    rows: account_code, debit, credit, description, partner_code, cost_center_code"""
    from apps.partners.models import Partner
    specs, errors = [], []
    for i, r in enumerate(rows, 1):
        try:
            acc = _account(company, r.get("account_code"))
        except BusinessError as e:
            errors.append(f"Row {i}: {e.message}")
            continue
        partner = None
        if r.get("partner_code"):
            partner = Partner.objects.filter(company=company, code=r["partner_code"]).first()
            if not partner:
                errors.append(f"Row {i}: unknown partner '{r['partner_code']}'.")
        cc = None
        if r.get("cost_center_code"):
            cc = CostCenter.objects.filter(company=company, code=r["cost_center_code"]).first()
            if not cc:
                errors.append(f"Row {i}: unknown cost center '{r['cost_center_code']}'.")
        specs.append(gl.LineSpec(account=acc, debit=D(r.get("debit")), credit=D(r.get("credit")),
                                 description=(r.get("description") or "")[:255], partner=partner, cost_center=cc))
    if errors:
        raise BusinessError("The journal was not imported.", "import_errors", {"errors": errors})
    total_d = sum((s.debit for s in specs), ZERO)
    total_c = sum((s.credit for s in specs), ZERO)
    if total_d != total_c:
        raise BusinessError(f"Imported lines do not balance: debit {total_d} vs credit {total_c}.", "unbalanced")
    e = gl.create_entry(company, journal, on_date, specs, user=user, reference="CSV import", narration=narration)
    return {"entry_id": e.pk, "lines": len(specs), "total": str(total_d)}
