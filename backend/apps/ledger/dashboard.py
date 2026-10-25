from datetime import date

from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.approvals.models import ApprovalRequest
from apps.billing.models import DocState, Invoice
from apps.core.money import ZERO
from apps.iam.access import ErpPermission, resolve_company

from .models import ACCOUNT_TYPES, AccountClass, JournalEntry, JournalLine

# account_type -> class, for grouping the denormalized JournalLine.account_type column
# (see models.py) without a join back to ledger_account.
INCOME_TYPES = [t for t, cls in ACCOUNT_TYPES.items() if cls == AccountClass.INCOME]
EXPENSE_TYPES = [t for t, cls in ACCOUNT_TYPES.items() if cls == AccountClass.EXPENSE]


def _bal(qs):
    # .order_by() with no args strips JournalLine's default Meta.ordering (entry_id, sequence,
    # id). Without it, .aggregate() on a plain (non-values/annotate) queryset still sorts by
    # that ordering before collapsing to the sum -- pointless for a scalar aggregate, but under
    # load testing at ~1M lines it forced a full external-merge sort of every matching row,
    # roughly 5x slower than the aggregate itself. .values().annotate() group-by queries
    # elsewhere in this codebase aren't affected (GROUP BY already drops row-level ordering).
    a = qs.order_by().aggregate(d=Sum("debit"), c=Sum("credit"))
    return (a["d"] or ZERO) - (a["c"] or ZERO)


class DashboardView(APIView):
    permission_classes = [ErpPermission]
    perms = {"get": "ledger.report.view"}

    def get(self, request):
        c = resolve_company(request)
        today = timezone.localdate()
        ys = date(today.year, c.fiscal_year_start_month, 1)
        if ys > today:
            ys = date(today.year - 1, c.fiscal_year_start_month, 1)
        ms = today.replace(day=1)
        L = JournalLine.objects.filter(company=c, posted=True)
        pl = L.filter(entry__is_closing=False)
        overdue = Invoice.objects.filter(company=c, state=DocState.POSTED, move_type="out_invoice",
                                         amount_residual__gt=0, due_date__lt=today)
        overdue_total = sum((i.amount_residual * i.rate for i in overdue), ZERO)
        months = []
        start = date(today.year - (1 if today.month < 12 else 0), (today.month % 12) + 1, 1)
        # Filter/group by the denormalized JournalLine.account_type (indexed) instead of
        # joining to ledger_account and filtering by account_class -- under load testing this
        # query showed up as a full sequential scan of the line table once it grew large.
        rows = (pl.filter(date__gte=start, account_type__in=INCOME_TYPES + EXPENSE_TYPES)
                .annotate(m=TruncMonth("date")).values("m", "account_type")
                .annotate(d=Sum("debit"), cr=Sum("credit")))
        agg = {}
        for r in rows:
            k = r["m"].strftime("%Y-%m")
            agg.setdefault(k, {"month": k, "income": ZERO, "expense": ZERO})
            if r["account_type"] in INCOME_TYPES:
                agg[k]["income"] += (r["cr"] or 0) - (r["d"] or 0)
            else:
                agg[k]["expense"] += (r["d"] or 0) - (r["cr"] or 0)
        y, m = start.year, start.month
        for _ in range(12):
            k = f"{y}-{m:02d}"
            v = agg.get(k, {"month": k, "income": ZERO, "expense": ZERO})
            months.append({"month": k, "income": str(v["income"]), "expense": str(v["expense"])})
            m += 1
            if m > 12:
                m, y = 1, y + 1
        income_ytd = -_bal(pl.filter(date__gte=ys, account_type__in=INCOME_TYPES))
        expense_ytd = _bal(pl.filter(date__gte=ys, account_type__in=EXPENSE_TYPES))
        return Response({
            "currency": c.base_currency_id,
            "cash": str(_bal(L.filter(account_type__in=["bank", "cash"]))),
            "receivables": str(_bal(L.filter(account_type="receivable"))),
            "payables": str(-_bal(L.filter(account_type="payable"))),
            "inventory": str(_bal(L.filter(account_type="inventory"))),
            "overdue_receivables": str(round(overdue_total, 2)), "overdue_count": overdue.count(),
            "revenue_mtd": str(-_bal(pl.filter(date__gte=ms, account_type="revenue"))),
            "revenue_ytd": str(-_bal(pl.filter(date__gte=ys, account_type="revenue"))),
            "net_profit_ytd": str(income_ytd - expense_ytd),
            "pending_approvals": ApprovalRequest.objects.filter(company=c, state="pending").count(),
            "draft_entries": JournalEntry.objects.filter(company=c, state="draft").count(),
            "monthly": months,
        })
