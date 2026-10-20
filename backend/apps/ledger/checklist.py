"""Month-end close checklist: everything an accounting team verifies before closing a period."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import F, Sum

from apps.core.money import ZERO

from .models import FiscalPeriod, JournalEntry, JournalLine


def _item(key, title, status, detail, count=None, link=None):
    return {"key": key, "title": title, "status": status, "detail": detail, "count": count, "link": link}


def run(company, period):
    from apps.assets.models import DepreciationLine
    from apps.assets.services import unregistered_capex
    from apps.banking.models import BankStatement, BankStatementLine
    from apps.billing.models import Invoice, Payment
    from apps.billing.revaluation import preview
    from apps.billing.services import subledger_reconciliation
    from apps.einvoice.models import EtaDocument, EtaReceivedDocument
    from apps.einvoice.services import eta_settings
    from apps.inventory.services import valuation_report
    from apps.ledger import reports
    from apps.ledger.models import Journal
    from apps.manufacturing.services import wip_report
    from apps.planning.models import RecurringEntry
    from apps.purchasing.models import PurchaseOrderLine

    start, end = period.start_date, period.end_date
    out = []
    drafts = JournalEntry.objects.filter(company=company, date__gte=start, date__lte=end).exclude(
        state__in=["posted", "cancelled"]).count()
    out.append(_item("drafts", "Unposted journal entries in the period", "ok" if not drafts else "fail",
                     f"{drafts} draft / waiting-approval entries", drafts, "/journal-entries?state=draft"))
    docs = Invoice.objects.filter(company=company, date__gte=start, date__lte=end,
                                  state__in=["draft", "to_approve", "approved"]).count()
    out.append(_item("draft_docs", "Unposted invoices and bills dated in the period", "ok" if not docs else "warn",
                     f"{docs} documents", docs, "/invoices"))
    rec = RecurringEntry.objects.filter(company=company, active=True, next_date__lte=end).count()
    out.append(_item("recurring", "Recurring entries due", "ok" if not rec else "warn", f"{rec} templates due", rec,
                     "/recurring"))
    dep = DepreciationLine.objects.filter(asset__company=company, posted=False, date__lte=end,
                                          asset__state="running").count()
    out.append(_item("depreciation", "Depreciation run up to period end", "ok" if not dep else "fail",
                     f"{dep} unposted depreciation lines", dep, "/assets"))
    capex = unregistered_capex(company).filter(invoice__date__lte=end).count()
    out.append(_item("capex", "Fixed-asset purchases registered in the asset register",
                     "ok" if not capex else "warn", f"{capex} bill lines on fixed-asset accounts without an asset",
                     capex, "/assets"))
    banks = Journal.objects.filter(company=company, type="bank", active=True)
    missing, unmatched = [], 0
    for j in banks:
        if not BankStatement.objects.filter(company=company, journal=j, date_to__gte=end).exists() and \
                JournalLine.objects.filter(company=company, account=j.default_account, posted=True,
                                           date__lte=end).exists():
            missing.append(j.code)
        unmatched += BankStatementLine.objects.filter(statement__journal=j, matched_line__isnull=True,
                                                      date__lte=end).count()
    status = "ok" if not missing and not unmatched else ("fail" if unmatched else "warn")
    out.append(_item("bank", "Bank accounts reconciled to statements", status,
                     (f"No statement up to period end for: {', '.join(missing)}. " if missing else "") +
                     f"{unmatched} unmatched statement lines", unmatched, "/banking"))
    chq = Payment.objects.filter(company=company, method="cheque", cheque_due_date__lte=end,
                                 cheque_state__in=["in_hand", "deposited", "issued"]).count()
    out.append(_item("cheques", "Cheques past due date not cleared", "ok" if not chq else "warn",
                     f"{chq} cheques to deposit / follow up", chq, "/cheques"))
    sl = subledger_reconciliation(company, end)
    ok = sl["receivable"]["reconciled"] and sl["payable"]["reconciled"]
    out.append(_item("subledger", "Receivables and payables agree with the GL", "ok" if ok else "fail",
                     f"AR difference {sl['receivable']['difference']}, AP difference {sl['payable']['difference']}",
                     None, "/reports?r=subledger-reconciliation"))
    val = valuation_report(company)
    agg = JournalLine.objects.filter(company=company, posted=True, account__account_type="inventory").aggregate(
        d=Sum("debit"), c=Sum("credit"))
    gl_inv = (agg["d"] or ZERO) - (agg["c"] or ZERO)
    out.append(_item("inventory", "Stock valuation agrees with the GL",
                     "ok" if gl_inv == Decimal(val["total_value"]) else "fail",
                     f"Stock {val['total_value']} vs GL {gl_inv}", None, "/reports?r=inventory-valuation"))
    wip = wip_report(company)
    out.append(_item("wip", "Work in progress agrees with open manufacturing orders",
                     "ok" if wip["reconciled"] else "fail", f"MOs {wip['total']} vs GL {wip['general_ledger']}",
                     len(wip["rows"]), "/manufacturing"))
    old = end - timedelta(days=60)
    grni = PurchaseOrderLine.objects.filter(order__company=company, order__state="confirmed",
                                            order__date__lte=old).exclude(qty_billed__gte=F("qty_received")).count()
    out.append(_item("grni", "Goods received over 60 days ago and not yet billed", "ok" if not grni else "warn",
                     f"{grni} purchase-order lines (accrue or chase the vendor)", grni, "/purchase-orders"))
    try:
        pending_fx = sum(len(v) for v in preview(company, end).values())
    except Exception:  # missing exchange rate
        pending_fx = -1
    revalued = JournalEntry.objects.filter(company=company, source_type="fx_revaluation", date=end,
                                           reversal_of__isnull=True, state="posted").exists()
    out.append(_item("fx", "Foreign-currency balances revalued at the closing rate",
                     "ok" if revalued or pending_fx == 0 else ("fail" if pending_fx == -1 else "warn"),
                     "Done" if revalued else ("Missing closing exchange rate" if pending_fx == -1 else
                                              f"{pending_fx} items to revalue"), None, "/revaluation"))
    st = eta_settings(company)
    if st and st.enabled:
        bad = EtaDocument.objects.filter(company=company, invoice__date__gte=start, invoice__date__lte=end).exclude(
            state__in=["valid", "cancelled"]).count()
        out.append(_item("eta", "All customer invoices accepted by ETA", "ok" if not bad else "fail",
                         f"{bad} documents not valid on the ETA portal", bad, "/eta"))
        unmatched_in = EtaReceivedDocument.objects.filter(company=company, bill__isnull=True,
                                                          date_issued__date__lte=end).count()
        out.append(_item("eta_in", "Vendor e-invoices on ETA recorded as bills", "ok" if not unmatched_in else "warn",
                         f"{unmatched_in} ETA documents from vendors with no matching bill", unmatched_in, "/eta"))
    obe = JournalLine.objects.filter(company=company, posted=True, account__code="320102").aggregate(
        d=Sum("debit"), c=Sum("credit"))
    obe_bal = (obe["d"] or ZERO) - (obe["c"] or ZERO)
    out.append(_item("obe", "Opening Balance Equity cleared after go-live", "ok" if not obe_bal else "warn",
                     f"Balance {obe_bal}", None, "/reports?r=general-ledger"))
    tb = reports.trial_balance(company, start, end)
    out.append(_item("tb", "Trial balance balances", "ok" if tb["balanced"] else "fail",
                     f"Debits {tb['totals']['debit']} / credits {tb['totals']['credit']}", None, "/reports"))
    summary = {s: sum(1 for i in out if i["status"] == s) for s in ("ok", "warn", "fail")}
    return {"period": {"id": period.pk, "name": period.name, "start": start, "end": end, "state": period.state},
            "items": out, "summary": summary, "ready_to_close": summary["fail"] == 0}
