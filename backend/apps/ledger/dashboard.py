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

from .models import JournalEntry, JournalLine


def _bal(qs):
    a = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
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
        rows = (pl.filter(date__gte=start, account__account_class__in=["income", "expense"])
                .annotate(m=TruncMonth("date")).values("m", "account__account_class")
                .annotate(d=Sum("debit"), cr=Sum("credit")))
        agg = {}
        for r in rows:
            k = r["m"].strftime("%Y-%m")
            agg.setdefault(k, {"month": k, "income": ZERO, "expense": ZERO})
            if r["account__account_class"] == "income":
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
        income_ytd = -_bal(pl.filter(date__gte=ys, account__account_class="income"))
        expense_ytd = _bal(pl.filter(date__gte=ys, account__account_class="expense"))
        return Response({
            "currency": c.base_currency_id,
            "cash": str(_bal(L.filter(account__account_type__in=["bank", "cash"]))),
            "receivables": str(_bal(L.filter(account__account_type="receivable"))),
            "payables": str(-_bal(L.filter(account__account_type="payable"))),
            "inventory": str(_bal(L.filter(account__account_type="inventory"))),
            "overdue_receivables": str(round(overdue_total, 2)), "overdue_count": overdue.count(),
            "revenue_mtd": str(-_bal(pl.filter(date__gte=ms, account__account_type="revenue"))),
            "revenue_ytd": str(-_bal(pl.filter(date__gte=ys, account__account_type="revenue"))),
            "net_profit_ytd": str(income_ytd - expense_ytd),
            "pending_approvals": ApprovalRequest.objects.filter(company=c, state="pending").count(),
            "draft_entries": JournalEntry.objects.filter(company=c, state="draft").count(),
            "monthly": months,
        })
