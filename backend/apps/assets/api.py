from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.billing.models import InvoiceLine
from apps.core.api import CompanyViewSet
from apps.core.exceptions import BusinessError
from apps.core.serializers_base import CompanyScopedSerializer
from apps.ledger.models import Account
from apps.ledger.views import parse_date

from . import services
from .models import Asset, AssetCategory, DepreciationLine


class AssetCategorySerializer(CompanyScopedSerializer):
    class Meta:
        model = AssetCategory
        fields = ["id", "name", "name_ar", "asset_account", "accumulated_account", "expense_account", "method",
                  "useful_life_months", "declining_rate", "tax_method", "tax_rate_pct"]


class DepreciationLineSerializer(serializers.ModelSerializer):
    entry_number = serializers.CharField(source="entry.number", read_only=True, default=None)

    class Meta:
        model = DepreciationLine
        fields = ["id", "date", "amount", "posted", "entry", "entry_number"]


class AssetSerializer(CompanyScopedSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    accumulated = serializers.DecimalField(max_digits=20, decimal_places=4, read_only=True)
    net_book_value = serializers.DecimalField(max_digits=20, decimal_places=4, read_only=True)
    bill_number = serializers.CharField(source="bill_line.invoice.number", read_only=True, default=None)

    class Meta:
        model = Asset
        fields = ["id", "code", "name", "name_ar", "category", "category_name", "serial_number", "location",
                  "custodian", "cost_center", "acquisition_date", "in_service_date", "depreciation_start", "cost",
                  "salvage_value", "opening_accumulated", "useful_life_months", "method", "declining_rate", "state",
                  "accumulated", "net_book_value", "bill_line", "bill_number", "disposal_date", "disposal_proceeds",
                  "disposal_entry", "notes"]
        read_only_fields = ["state", "bill_line", "disposal_date", "disposal_proceeds", "disposal_entry"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance and self.instance.state != "draft":
            locked = {"cost", "salvage_value", "opening_accumulated", "useful_life_months", "method", "category",
                      "in_service_date", "depreciation_start", "declining_rate"}
            if locked & set(attrs):
                raise serializers.ValidationError("Depreciation parameters of a confirmed asset cannot change.")
        cat = attrs.get("category")
        if cat and not self.instance:
            attrs.setdefault("useful_life_months", cat.useful_life_months)
            attrs.setdefault("method", cat.method)
            attrs.setdefault("declining_rate", cat.declining_rate)
        return attrs


class AssetCategoryViewSet(CompanyViewSet):
    queryset = AssetCategory.objects.all()
    serializer_class = AssetCategorySerializer
    perms = {"list": "assets.asset.view", "*": "assets.asset.manage"}
    pagination_class = None


class AssetViewSet(CompanyViewSet):
    queryset = Asset.objects.select_related("category", "bill_line__invoice")
    serializer_class = AssetSerializer
    perms = {"list": "assets.asset.view", "schedule": "assets.asset.view", "register": "assets.asset.view",
             "tax_depreciation": "assets.asset.view", "unregistered": "assets.asset.view",
             "create": "assets.asset.manage", "update": "assets.asset.manage", "destroy": "assets.asset.manage",
             "confirm": "assets.asset.manage", "from_bill_line": "assets.asset.manage",
             "run_depreciation": "assets.depreciation.run", "dispose": "assets.asset.dispose"}
    filterset_fields = ["state", "category"]
    search_fields = ["code", "name", "serial_number", "custodian"]

    def perform_destroy(self, instance):
        if instance.state != "draft":
            raise BusinessError("Only draft assets can be deleted.", "not_draft")
        instance.delete()

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        return Response(self.get_serializer(services.confirm(self.get_object(), request.user)).data)

    @action(detail=True)
    def schedule(self, request, pk=None):
        return Response(DepreciationLineSerializer(self.get_object().lines.select_related("entry"), many=True).data)

    @action(detail=True, methods=["post"])
    def dispose(self, request, pk=None):
        acc = None
        if request.data.get("proceeds_account"):
            acc = get_object_or_404(Account, pk=request.data["proceeds_account"], company=self.company)
        a = services.dispose(self.get_object(), parse_date(request.data.get("date"), timezone.localdate()),
                             request.data.get("proceeds") or 0, acc, request.user, request.data.get("reason", ""))
        return Response(self.get_serializer(a).data)

    @action(detail=False, methods=["post"], url_path="run-depreciation")
    def run_depreciation(self, request):
        entries = services.run_depreciation(self.company, parse_date(request.data.get("up_to"), timezone.localdate()),
                                            request.user)
        return Response({"entries": [e.number for e in entries]})

    @action(detail=False, methods=["post"], url_path="from-bill-line")
    def from_bill_line(self, request):
        line = get_object_or_404(InvoiceLine, pk=request.data.get("line"), invoice__company=self.company)
        cat = None
        if request.data.get("category"):
            cat = get_object_or_404(AssetCategory, pk=request.data["category"], company=self.company)
        return Response(self.get_serializer(services.create_from_bill_line(line, request.user, cat)).data, status=201)

    @action(detail=False)
    def unregistered(self, request):
        return Response([{"line": l.pk, "bill": l.invoice.number, "date": l.invoice.date, "description": l.description,
                          "account": f"{l.account.code} {l.account.name}", "amount": str(l.subtotal),
                          "currency": l.invoice.currency_id} for l in services.unregistered_capex(self.company)])

    @action(detail=False)
    def register(self, request):
        return Response(services.register(self.company, parse_date(request.query_params.get("as_of"),
                                                                   timezone.localdate())))

    @action(detail=False, url_path="tax-depreciation")
    def tax_depreciation(self, request):
        return Response(services.tax_depreciation(self.company, int(request.query_params.get(
            "year", timezone.localdate().year))))
