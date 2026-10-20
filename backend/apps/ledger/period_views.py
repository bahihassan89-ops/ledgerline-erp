"""Period-end and data endpoints: close checklist, FX revaluation, imports."""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.access import ErpPermission, resolve_company

from . import checklist, imports
from .models import FiscalPeriod, Journal
from .views import parse_date


class ChecklistView(APIView):
    permission_classes = [ErpPermission]
    perms = {"get": "ledger.report.view"}

    def get(self, request):
        c = resolve_company(request)
        pid = request.query_params.get("period")
        if pid:
            period = get_object_or_404(FiscalPeriod, pk=pid, company=c)
        else:
            d = timezone.localdate()
            period = FiscalPeriod.objects.filter(company=c, start_date__lte=d, end_date__gte=d).first() or \
                FiscalPeriod.objects.filter(company=c).order_by("-start_date").first()
        return Response(checklist.run(c, period))


class RevaluationView(APIView):
    permission_classes = [ErpPermission]
    perms = {"get": "ledger.report.view", "post": "ledger.revaluation.run"}

    def get(self, request):
        from apps.billing.revaluation import preview
        c = resolve_company(request)
        items = preview(c, parse_date(request.query_params.get("date"), timezone.localdate()))
        return Response({cur: [{"kind": i["kind"], "number": i["number"],
                                "partner": i["partner"].name if i["partner"] else "",
                                "account": i["account"].code, "open": str(i["open"]), "rate": str(i["rate"]),
                                "booked_rate": str(i["booked_rate"]) if i["booked_rate"] else None,
                                "adjustment": str(i["correction"])} for i in rows] for cur, rows in items.items()})

    def post(self, request):
        from apps.billing.revaluation import revalue
        c = resolve_company(request)
        entries = revalue(c, parse_date(request.data.get("date"), timezone.localdate()), request.user)
        return Response({"entries": [e.number for e in entries]})


class ImportView(APIView):
    """POST /api/imports/<kind>/  kind = opening-balances | open-items | journal"""
    permission_classes = [ErpPermission]
    perms = {"post": "ledger.import.run"}

    def post(self, request, kind):
        c = resolve_company(request)
        rows = request.data.get("rows") or []
        d = parse_date(request.data.get("date"), timezone.localdate())
        if kind == "opening-balances":
            return Response(imports.opening_trial_balance(c, d, rows, request.user), status=201)
        if kind == "open-items":
            return Response(imports.open_items(c, request.data.get("kind", "receivable"), rows, request.user),
                            status=201)
        if kind == "journal":
            j = get_object_or_404(Journal, pk=request.data.get("journal"), company=c)
            return Response(imports.journal_csv(c, j, d, request.data.get("narration", "Imported journal"), rows,
                                                request.user), status=201)
        return Response({"detail": "Unknown import."}, status=404)
