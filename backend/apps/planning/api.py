from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import CompanyViewSet
from apps.core.exceptions import BusinessError
from apps.core.models import CostCenter
from apps.core.serializers_base import CompanyScopedSerializer
from apps.ledger.views import parse_date

from . import services
from .models import Budget, RecurringEntry


class BudgetSerializer(CompanyScopedSerializer):
    fiscal_year_name = serializers.CharField(source="fiscal_year.name", read_only=True)

    class Meta:
        model = Budget
        fields = ["id", "name", "fiscal_year", "fiscal_year_name", "state", "notes"]
        read_only_fields = ["state"]


class BudgetViewSet(CompanyViewSet):
    queryset = Budget.objects.select_related("fiscal_year")
    serializer_class = BudgetSerializer
    perms = {"list": "planning.budget.view", "lines": ["planning.budget.view", "planning.budget.manage"],
             "report": "planning.budget.view", "*": "planning.budget.manage"}
    pagination_class = None

    @action(detail=True, methods=["get", "put"])
    def lines(self, request, pk=None):
        b = self.get_object()
        if request.method == "PUT":
            if "planning.budget.manage" not in request.user.permissions_for(self.company.pk):
                raise BusinessError("Missing permission: planning.budget.manage", "forbidden")
            services.set_budget_lines(b, request.data.get("lines", []))
        return Response([{"account": l.account_id, "cost_center": l.cost_center_id, "period": l.period_id,
                          "amount": str(l.amount)} for l in b.lines.all()])

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        b = self.get_object()
        b.state = "approved"
        b.save(update_fields=["state", "updated_at"])
        return Response(self.get_serializer(b).data)

    @action(detail=True)
    def report(self, request, pk=None):
        b = self.get_object()
        q = request.query_params
        cc = get_object_or_404(CostCenter, pk=q["cost_center"], company=self.company) if q.get("cost_center") else None
        return Response(services.budget_vs_actual(b, parse_date(q.get("date_from"), b.fiscal_year.start_date),
                                                  parse_date(q.get("date_to"), b.fiscal_year.end_date), cc))


class RecurringSerializer(CompanyScopedSerializer):
    journal_code = serializers.CharField(source="journal.code", read_only=True)
    last_entry_number = serializers.CharField(source="last_entry.number", read_only=True, default=None)

    class Meta:
        model = RecurringEntry
        fields = ["id", "name", "journal", "journal_code", "narration", "lines", "frequency", "next_date", "end_date",
                  "auto_post", "auto_reverse_days", "active", "last_entry", "last_entry_number"]
        read_only_fields = ["last_entry"]

    def validate_lines(self, lines):
        from decimal import Decimal
        if len(lines) < 2:
            raise serializers.ValidationError("At least two lines.")
        d = sum(Decimal(str(l.get("debit") or 0)) for l in lines)
        c = sum(Decimal(str(l.get("credit") or 0)) for l in lines)
        if d != c or not d:
            raise serializers.ValidationError("Template lines must balance.")
        from apps.ledger.models import Account
        company = self.context["company"]
        ids = [l.get("account") for l in lines]
        if Account.objects.filter(company=company, pk__in=ids).count() != len(set(ids)):
            raise serializers.ValidationError("Unknown account in template.")
        return lines


class RecurringViewSet(CompanyViewSet):
    queryset = RecurringEntry.objects.select_related("journal", "last_entry")
    serializer_class = RecurringSerializer
    perms = {"list": "ledger.entry.view", "*": "planning.recurring.manage"}
    pagination_class = None

    @action(detail=False, methods=["post"], url_path="run-due")
    def run_due(self, request):
        created = services.run_due(self.company, parse_date(request.data.get("date"), timezone.localdate()),
                                   request.user)
        return Response({"created": [e.number or f"draft #{e.pk}" for e in created]})
