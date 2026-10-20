"""Fixed assets (EAS 10): register, monthly depreciation, disposal; tax depreciation per
Egyptian Income Tax Law 91/2005 art. 25 for the tax-return reconciliation."""
import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from apps.core import audit
from apps.core.exceptions import BusinessError
from apps.core.money import ZERO, D, round_to
from apps.ledger import services as gl
from apps.ledger.models import JournalLine

from .models import Asset, AssetCategory, AssetState, DepreciationLine, Method, TaxMethod


def month_end(d):
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def add_months(d, n):
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, calendar.monthrange(y, m)[1])


def months_between(a, b):
    """Whole months from month of a to month of b."""
    return (b.year - a.year) * 12 + (b.month - a.month)


# (name, name_ar, asset, accumulated, expense, method, life months, declining %, tax method, tax rate)
DEFAULT_CATEGORIES = [
    ("Land", "أراضي", "120101", None, None, Method.STRAIGHT_LINE, 1, 0, TaxMethod.NONE, 0),
    ("Buildings", "مباني وإنشاءات", "120102", "120202", "520501", Method.STRAIGHT_LINE, 600, 0,
     TaxMethod.STRAIGHT_ON_COST, 5),
    ("Machinery & Equipment", "آلات ومعدات", "120103", "120203", "520501", Method.STRAIGHT_LINE, 120, 0,
     TaxMethod.POOL_DECLINING, 25),
    ("Motor Vehicles", "وسائل نقل وانتقال", "120104", "120204", "520501", Method.STRAIGHT_LINE, 60, 0,
     TaxMethod.POOL_DECLINING, 25),
    ("Furniture & Fixtures", "أثاث وتجهيزات", "120105", "120205", "520501", Method.STRAIGHT_LINE, 120, 0,
     TaxMethod.POOL_DECLINING, 25),
    ("Computers & IT", "حاسبات آلية ونظم معلومات", "120106", "120206", "520501", Method.STRAIGHT_LINE, 36, 0,
     TaxMethod.POOL_DECLINING, 50),
    ("Software & Licences", "برامج وتراخيص", "120301", "120302", "520502", Method.STRAIGHT_LINE, 36, 0,
     TaxMethod.POOL_DECLINING, 50),
]


def ensure_default_categories(company, acc):
    for name, ar, a, ac, ex, method, life, dr, tm, tr in DEFAULT_CATEGORIES:
        if a not in acc:
            continue
        AssetCategory.objects.get_or_create(company=company, name=name, defaults=dict(
            name_ar=ar, asset_account=acc[a], accumulated_account=acc.get(ac) if ac else None,
            expense_account=acc.get(ex) if ex else None, method=method, useful_life_months=life,
            declining_rate=dr, tax_method=tm, tax_rate_pct=tr))


@transaction.atomic
def compute_schedule(asset):
    """(Re)build the unposted part of the schedule. Posted lines are never touched."""
    asset.lines.filter(posted=False).delete()
    if not asset.category.accumulated_account_id:  # land: not depreciated
        return []
    decimals = asset.company.base_currency.decimals
    posted = list(asset.lines.filter(posted=True).order_by("date"))
    start = month_end(asset.depreciation_start or asset.in_service_date)
    if posted:
        start = add_months(posted[-1].date, 1)
    elapsed = months_between(asset.in_service_date, start)
    remaining = asset.useful_life_months - elapsed
    nbv = asset.cost - asset.opening_accumulated - sum((l.amount for l in posted), ZERO)
    depreciable = nbv - asset.salvage_value
    out = []
    if depreciable <= 0 or remaining <= 0:
        return out
    if asset.method == Method.STRAIGHT_LINE:
        monthly = round_to(depreciable / remaining, decimals)
        for i in range(remaining):
            amt = monthly if i < remaining - 1 else depreciable - monthly * (remaining - 1)
            if amt > 0:
                out.append(DepreciationLine(asset=asset, date=add_months(start, i), amount=amt))
    else:
        rate = D(asset.declining_rate) / Decimal(1200)
        left = depreciable
        for i in range(remaining):
            amt = left if i == remaining - 1 else min(left, round_to((nbv - (depreciable - left)) * rate, decimals))
            if amt <= 0:
                break
            out.append(DepreciationLine(asset=asset, date=add_months(start, i), amount=amt))
            left -= amt
    DepreciationLine.objects.bulk_create(out)
    return out


@transaction.atomic
def confirm(asset, user):
    asset = Asset.objects.select_for_update().select_related("category").get(pk=asset.pk)
    if asset.state != AssetState.DRAFT:
        raise BusinessError("Only draft assets can be confirmed.", "invalid_state")
    cat = asset.category
    if cat.accumulated_account_id and not cat.expense_account_id:
        raise BusinessError(f"Category {cat} needs a depreciation expense account.", "missing_account")
    if asset.in_service_date < asset.acquisition_date:
        raise BusinessError("In-service date cannot be before acquisition date.", "invalid_date")
    if asset.opening_accumulated >= asset.cost - asset.salvage_value and asset.opening_accumulated:
        asset.state = AssetState.FULLY_DEPRECIATED
    else:
        asset.state = AssetState.RUNNING
    asset.save(update_fields=["state", "updated_at"])
    compute_schedule(asset)
    audit.record("confirm", asset, {"cost": asset.cost, "life": asset.useful_life_months})
    return asset


@transaction.atomic
def run_depreciation(company, up_to, user, assets=None):
    """Post every unposted depreciation line dated on or before `up_to`: one journal entry per month."""
    qs = (DepreciationLine.objects.select_for_update()
          .filter(asset__company=company, posted=False, date__lte=up_to, asset__state=AssetState.RUNNING)
          .select_related("asset", "asset__category").order_by("date", "asset__code"))
    if assets is not None:
        qs = qs.filter(asset__in=assets)
    by_month = defaultdict(list)
    for ln in qs:
        by_month[ln.date].append(ln)
    journal = gl.journal_for(company, "depreciation_journal", "depreciation")
    entries = []
    for d in sorted(by_month):
        lines = by_month[d]
        specs = []
        for ln in lines:
            a, cat = ln.asset, ln.asset.category
            specs.append(gl.LineSpec(account=cat.expense_account, debit=ln.amount, cost_center=a.cost_center,
                                     description=f"Depreciation {a.code} {a.name}"[:255]))
            specs.append(gl.LineSpec(account=cat.accumulated_account, credit=ln.amount,
                                     description=f"Depreciation {a.code}"))
        entry = gl.create_entry(company, journal, d, specs, user=user, reference=f"DEP {d:%Y-%m}",
                                narration=f"Depreciation for {d:%B %Y} ({len(lines)} assets)",
                                source=("depreciation", int(d.strftime("%Y%m"))))
        gl.post_entry(entry, user, allow_control=True)
        for ln in lines:
            ln.posted, ln.entry = True, entry
            ln.save(update_fields=["posted", "entry"])
        entries.append(entry)
    for a in Asset.objects.filter(company=company, state=AssetState.RUNNING):
        if not a.lines.filter(posted=False).exists() and a.category.accumulated_account_id:
            a.state = AssetState.FULLY_DEPRECIATED
            a.save(update_fields=["state", "updated_at"])
    if entries:
        audit.record("run_depreciation", entries[-1], {"up_to": up_to, "entries": [e.number for e in entries]})
    return entries


@transaction.atomic
def dispose(asset, on_date, proceeds, proceeds_account, user, reason=""):
    """Sale or scrapping. Depreciation is posted up to the disposal date first.
    Dr Accumulated depreciation, Dr Proceeds account, Cr Asset cost, ± gain/loss on disposal."""
    asset = Asset.objects.select_for_update().select_related("category", "company").get(pk=asset.pk)
    company = asset.company
    if asset.state not in (AssetState.RUNNING, AssetState.FULLY_DEPRECIATED):
        raise BusinessError("Only assets in service can be disposed.", "invalid_state")
    proceeds = D(proceeds)
    if proceeds < 0:
        raise BusinessError("Proceeds cannot be negative.", "invalid_amount")
    if proceeds and (proceeds_account is None or proceeds_account.reconcilable or proceeds_account.is_group):
        raise BusinessError("Choose a bank, cash or other-receivable account for the proceeds "
                            "(invoice the buyer separately if needed).", "invalid_account")
    run_depreciation(company, on_date, user, assets=[asset])
    asset.lines.filter(posted=False).delete()
    cat = asset.category
    accumulated = asset.accumulated
    nbv = asset.cost - accumulated
    result = proceeds - nbv  # + gain, - loss
    st = gl.settings_for(company)
    specs = [gl.LineSpec(account=cat.asset_account, credit=asset.cost, description=f"Disposal {asset.code}")]
    if accumulated:
        specs.append(gl.LineSpec(account=cat.accumulated_account, debit=accumulated,
                                 description=f"Disposal {asset.code} — accumulated depreciation"))
    if proceeds:
        specs.append(gl.LineSpec(account=proceeds_account, debit=proceeds, description=f"Proceeds {asset.code}"))
    if result > 0:
        specs.append(gl.LineSpec(account=gl.require_account(st.asset_disposal_gain_account, "gain on disposal"),
                                 credit=result, description=f"Gain on disposal {asset.code}"))
    elif result < 0:
        specs.append(gl.LineSpec(account=gl.require_account(st.asset_disposal_loss_account, "loss on disposal"),
                                 debit=-result, description=f"Loss on disposal {asset.code}"))
    entry = gl.create_entry(company, gl.journal_for(company, "general_journal", "general"), on_date, specs,
                            user=user, reference=f"Disposal {asset.code}",
                            narration=reason or f"Disposal of fixed asset {asset}", source=("asset", asset.pk))
    gl.post_entry(entry, user, allow_control=True)
    asset.state = AssetState.DISPOSED
    asset.disposal_date, asset.disposal_proceeds, asset.disposal_entry = on_date, proceeds, entry
    asset.save()
    audit.record("dispose", asset, {"proceeds": proceeds, "nbv": nbv, "result": result, "entry": entry.number})
    return asset


@transaction.atomic
def create_from_bill_line(line, user, category=None, code=None):
    """Register a fixed asset from a posted vendor-bill line that was booked to a fixed-asset account."""
    inv = line.invoice
    if inv.state != "posted" or inv.move_type != "in_invoice":
        raise BusinessError("The bill must be posted.", "invalid_state")
    if line.account.account_type not in ("fixed_asset", "non_current_asset"):
        raise BusinessError("This bill line was not booked to a fixed-asset account.", "not_capex")
    if Asset.objects.filter(bill_line=line).exists():
        raise BusinessError("An asset already exists for this bill line.", "duplicate")
    category = category or AssetCategory.objects.filter(company=inv.company, asset_account=line.account).first()
    if not category:
        raise BusinessError("No asset category uses this account. Choose a category.", "no_category")
    cost = gl.to_base(inv.company, line.subtotal, inv.rate)
    n = Asset.objects.filter(company=inv.company).count() + 1
    asset = Asset.objects.create(
        company=inv.company, code=code or f"FA-{n:05d}", name=line.description[:200], category=category,
        acquisition_date=inv.date, in_service_date=inv.date, cost=cost, useful_life_months=category.useful_life_months,
        method=category.method, declining_rate=category.declining_rate, cost_center=line.cost_center,
        bill_line=line, created_by=user)
    audit.record("create_from_bill", asset, {"bill": inv.number, "cost": cost})
    return asset


def unregistered_capex(company):
    """Posted bill lines on fixed-asset accounts with no asset record — a month-end control."""
    from apps.billing.models import InvoiceLine
    return (InvoiceLine.objects.filter(invoice__company=company, invoice__state="posted", invoice__move_type="in_invoice",
                                       account__account_type__in=["fixed_asset", "non_current_asset"],
                                       asset__isnull=True).select_related("invoice", "account"))


def register(company, as_of):
    rows = []
    totals = defaultdict(lambda: ZERO)
    for a in Asset.objects.filter(company=company, acquisition_date__lte=as_of).exclude(state=AssetState.DRAFT) \
            .select_related("category"):
        if a.state == AssetState.DISPOSED and a.disposal_date and a.disposal_date <= as_of:
            continue
        dep = a.lines.filter(posted=True, date__lte=as_of).aggregate(s=Sum("amount"))["s"] or ZERO
        acc = a.opening_accumulated + dep
        rows.append({"id": a.pk, "code": a.code, "name": a.name, "name_ar": a.name_ar, "category": a.category.name,
                     "in_service_date": a.in_service_date, "cost": str(a.cost), "accumulated": str(acc),
                     "net_book_value": str(a.cost - acc), "state": a.state, "method": a.method,
                     "useful_life_months": a.useful_life_months, "location": a.location, "custodian": a.custodian})
        totals["cost"] += a.cost
        totals["accumulated"] += acc
    # Reconcile with the GL
    def gl_bal(types):
        agg = JournalLine.objects.filter(company=company, posted=True, date__lte=as_of,
                                         account__account_type__in=types).aggregate(d=Sum("debit"), c=Sum("credit"))
        return (agg["d"] or ZERO) - (agg["c"] or ZERO)
    gl_cost = gl_bal(["fixed_asset", "non_current_asset"])
    gl_acc = -gl_bal(["accumulated_depreciation"])
    return {"as_of": as_of, "rows": rows, "total_cost": str(totals["cost"]),
            "total_accumulated": str(totals["accumulated"]),
            "total_nbv": str(totals["cost"] - totals["accumulated"]),
            "gl_cost": str(gl_cost), "gl_accumulated": str(gl_acc),
            "reconciled": gl_cost == totals["cost"] and gl_acc == totals["accumulated"]}


def tax_depreciation(company, year):
    """Tax depreciation for `year` (calendar year of the tax period) per category, Law 91/2005 art. 25:
    * straight_on_cost: rate × cost of assets in use during the year;
    * pool_declining: rate × depreciation basis, where basis = opening basis + additions − disposal proceeds
      (art. 26). Computed from the first in-service year so the opening basis is consistent.
    Shown next to accounting depreciation; the difference feeds the tax-return adjustment."""
    out = []
    for cat in AssetCategory.objects.filter(company=company):
        assets = list(cat.assets.exclude(state=AssetState.DRAFT))
        if not assets:
            continue
        acct = DepreciationLine.objects.filter(asset__in=assets, posted=True, date__year=year).aggregate(
            s=Sum("amount"))["s"] or ZERO
        rate = D(cat.tax_rate_pct) / 100
        tax = ZERO
        basis_close = None
        if cat.tax_method == TaxMethod.STRAIGHT_ON_COST:
            for a in assets:
                if a.in_service_date.year <= year and not (a.disposal_date and a.disposal_date.year < year):
                    depreciated_before = rate * a.cost * max(0, year - a.in_service_date.year)
                    tax += min(rate * a.cost, max(ZERO, a.cost - depreciated_before))
        elif cat.tax_method == TaxMethod.POOL_DECLINING:
            first = min(a.in_service_date.year for a in assets)
            basis = ZERO
            for y in range(first, year + 1):
                additions = sum((a.cost for a in assets if a.in_service_date.year == y), ZERO)
                proceeds = sum((a.disposal_proceeds for a in assets if a.disposal_date and a.disposal_date.year == y),
                               ZERO)
                basis = max(ZERO, basis + additions - proceeds)
                dep = round_to(basis * rate, 2)
                if y == year:
                    tax = dep
                basis -= dep
            basis_close = basis
        out.append({"category": cat.name, "tax_method": cat.tax_method, "tax_rate_pct": str(cat.tax_rate_pct),
                    "accounting_depreciation": str(acct), "tax_depreciation": str(round_to(tax, 2)),
                    "difference": str(round_to(tax - acct, 2)),
                    "closing_tax_basis": str(round_to(basis_close, 2)) if basis_close is not None else None})
    return {"year": year, "rows": out,
            "note": "Reference computation under Income Tax Law 91/2005 arts. 25–27. Confirm with your tax advisor; "
                    "the optional 30% first-year deduction for production machinery (art. 27) is not applied."}
