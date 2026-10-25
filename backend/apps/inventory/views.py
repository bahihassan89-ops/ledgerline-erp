from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import CompanyReadOnlyViewSet, CompanyViewSet
from apps.core.exceptions import BusinessError
from apps.integrations.events import emit
from apps.ledger.models import Account
from apps.ledger.views import parse_date

from . import services
from .models import Lot, MoveType, Product, ProductCategory, SerialNumber, StockMove, StockQuant, Warehouse
from .serializers import (LotSerializer, ProductCategorySerializer, ProductSerializer, SerialNumberSerializer,
                          StockMoveSerializer, StockQuantSerializer, WarehouseSerializer)


class ProductCategoryViewSet(CompanyViewSet):
    queryset = ProductCategory.objects.all()
    serializer_class = ProductCategorySerializer
    perms = {"list": "inventory.product.view", "*": "inventory.product.manage"}
    pagination_class = None


class ProductViewSet(CompanyViewSet):
    queryset = Product.objects.select_related("category", "valuation")
    serializer_class = ProductSerializer
    perms = {"list": "inventory.product.view", "*": "inventory.product.manage"}
    filterset_fields = ["type", "category", "active"]
    search_fields = ["sku", "name", "barcode"]
    ordering_fields = ["sku", "name", "sale_price"]

    def perform_create(self, serializer):
        p = serializer.save(company=self.company, created_by=self.request.user)
        emit(self.company, "product.created", ProductSerializer(p).data)

    def perform_update(self, serializer):
        p = serializer.save()
        emit(self.company, "product.updated", ProductSerializer(p).data)

    def perform_destroy(self, instance):
        if instance.moves.exists():
            raise BusinessError("Product has stock moves; deactivate it instead.", "in_use")
        instance.delete()

    @action(detail=False, url_path="reorder-suggestions")
    def reorder_suggestions(self, request):
        """Simple MRP gap analysis: which products are (or will be) below their reorder level once
        open purchase/manufacturing supply and open sales/manufacturing demand are netted off --
        see services.reorder_suggestions."""
        return Response(services.reorder_suggestions(self.company))


class WarehouseViewSet(CompanyViewSet):
    queryset = Warehouse.objects.all()
    serializer_class = WarehouseSerializer
    perms = {"list": "inventory.stock.view", "*": "inventory.product.manage"}
    pagination_class = None


class StockQuantViewSet(CompanyReadOnlyViewSet):
    queryset = StockQuant.objects.select_related("product", "warehouse").filter(quantity__gt=0)
    serializer_class = StockQuantSerializer
    perms = {"list": "inventory.stock.view"}
    filterset_fields = ["product", "warehouse"]


class LotViewSet(CompanyViewSet):
    queryset = Lot.objects.select_related("product")
    serializer_class = LotSerializer
    perms = {"list": "inventory.stock.view", "genealogy": "inventory.stock.view", "*": "inventory.product.manage"}
    pagination_class = None
    filterset_fields = ["product"]
    search_fields = ["lot_number", "product__sku"]

    @action(detail=True)
    def genealogy(self, request, pk=None):
        return Response(services.lot_genealogy(self.get_object()))


class SerialNumberViewSet(CompanyViewSet):
    queryset = SerialNumber.objects.select_related("product", "lot", "warehouse")
    serializer_class = SerialNumberSerializer
    perms = {"list": "inventory.stock.view", "history": "inventory.stock.view", "*": "inventory.product.manage"}
    pagination_class = None
    filterset_fields = ["product", "status", "warehouse"]
    search_fields = ["serial_number", "product__sku"]

    @action(detail=True)
    def history(self, request, pk=None):
        return Response(services.serial_history(self.get_object()))


class StockOpSerializer(serializers.Serializer):
    product = serializers.IntegerField()
    warehouse = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4, required=False)
    counted_qty = serializers.DecimalField(max_digits=18, decimal_places=4, required=False)
    unit_cost = serializers.DecimalField(max_digits=20, decimal_places=6, required=False)
    to_warehouse = serializers.IntegerField(required=False)
    counter_account = serializers.IntegerField(required=False)
    date = serializers.DateField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=200)
    lot_number = serializers.CharField(required=False, allow_blank=True, max_length=60)
    serials = serializers.ListField(child=serializers.CharField(max_length=80), required=False)


class StockMoveViewSet(CompanyReadOnlyViewSet):
    queryset = StockMove.objects.select_related("product", "warehouse", "journal_entry")
    serializer_class = StockMoveSerializer
    perms = {"list": "inventory.stock.view", "adjust": "inventory.stock.adjust", "transfer": "inventory.stock.move",
             "opening": "inventory.stock.adjust"}
    http_method_names = ["get", "post", "head", "options"]
    filterset_fields = {"product": ["exact"], "warehouse": ["exact"], "move_type": ["exact"],
                        "date": ["gte", "lte"]}
    search_fields = ["number", "reference", "product__sku"]

    def _load(self, request):
        s = StockOpSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        c = self.company
        v["product"] = get_object_or_404(Product, pk=v["product"], company=c)
        v["warehouse"] = get_object_or_404(Warehouse, pk=v["warehouse"], company=c)
        v["date"] = v.get("date") or timezone.localdate()
        return v

    @action(detail=False, methods=["post"])
    def adjust(self, request):
        v = self._load(request)
        if v.get("counted_qty") is None:
            raise serializers.ValidationError({"counted_qty": "Required."})
        m = services.adjust(self.company, v["product"], v["warehouse"], v["counted_qty"], v["date"], request.user,
                            unit_cost=v.get("unit_cost"), reason=v.get("reason", ""))
        return Response(StockMoveSerializer(m).data, status=201)

    @action(detail=False, methods=["post"])
    def opening(self, request):
        """Opening stock: Dr Inventory / Cr the chosen opening-balance (equity) account."""
        v = self._load(request)
        if not v.get("quantity") or v.get("unit_cost") is None or not v.get("counter_account"):
            raise serializers.ValidationError("quantity, unit_cost and counter_account are required.")
        acc = get_object_or_404(Account, pk=v["counter_account"], company=self.company)
        m = services.receive(self.company, v["product"], v["warehouse"], v["quantity"], v["unit_cost"], v["date"],
                             request.user, counter_account=acc, move_type=MoveType.OPENING,
                             reference="Opening stock", lot_number=v.get("lot_number"), serials=v.get("serials"))
        return Response(StockMoveSerializer(m).data, status=201)

    @action(detail=False, methods=["post"])
    def transfer(self, request):
        v = self._load(request)
        dst = get_object_or_404(Warehouse, pk=v.get("to_warehouse"), company=self.company)
        m = services.transfer(self.company, v["product"], v["warehouse"], dst, v.get("quantity") or 0, v["date"],
                              request.user, v.get("reason", ""))
        return Response(StockMoveSerializer(m).data, status=201)


class LandedCostSerializer(serializers.ModelSerializer):
    entry_number = serializers.CharField(source="journal_entry.number", read_only=True, default=None)

    class Meta:
        from .models import LandedCost
        model = LandedCost
        fields = ["id", "number", "date", "description", "amount", "allocation", "counter_account", "moves", "state",
                  "journal_entry", "entry_number"]
        read_only_fields = ["number", "state", "journal_entry"]

    def validate(self, attrs):
        company = self.context["company"]
        for m in attrs.get("moves", []):
            if m.company_id != company.pk:
                raise serializers.ValidationError({"moves": "Receipt belongs to another company."})
        acc = attrs.get("counter_account")
        if acc and acc.company_id != company.pk:
            raise serializers.ValidationError({"counter_account": "Account belongs to another company."})
        if self.instance and self.instance.state != "draft":
            raise serializers.ValidationError("Posted landed costs cannot be edited.")
        return attrs


class LandedCostViewSet(CompanyViewSet):
    from .models import LandedCost as _LC
    queryset = _LC.objects.select_related("journal_entry").prefetch_related("moves")
    serializer_class = LandedCostSerializer
    perms = {"list": "inventory.valuation.view", "*": "inventory.landed_cost.manage"}

    def perform_destroy(self, instance):
        if instance.state != "draft":
            raise BusinessError("Posted landed costs cannot be deleted.", "not_draft")
        instance.delete()

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        return Response(self.get_serializer(services.post_landed_cost(self.get_object(), request.user)).data)
