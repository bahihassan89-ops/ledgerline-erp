from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import CompanyViewSet
from apps.core.exceptions import BusinessError
from apps.core.serializers_base import CompanyScopedSerializer
from apps.ledger.views import parse_date

from . import services
from .models import Bom, BomComponent, BomOperation, ManufacturingOrder, MoComponent, MoOperation, WorkCenter


class WorkCenterSerializer(CompanyScopedSerializer):
    class Meta:
        model = WorkCenter
        fields = ["id", "code", "name", "labour_rate", "overhead_rate", "cost_center", "active"]


class BomComponentSerializer(CompanyScopedSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)

    class Meta:
        model = BomComponent
        fields = ["id", "product", "product_sku", "quantity", "scrap_pct"]


class BomOperationSerializer(CompanyScopedSerializer):
    class Meta:
        model = BomOperation
        fields = ["id", "sequence", "name", "work_center", "minutes"]


class BomSerializer(CompanyScopedSerializer):
    components = BomComponentSerializer(many=True)
    operations = BomOperationSerializer(many=True, required=False)
    product_name = serializers.CharField(source="product.name", read_only=True)
    planned_unit_cost = serializers.SerializerMethodField()

    class Meta:
        model = Bom
        fields = ["id", "code", "product", "product_name", "quantity", "active", "notes", "components", "operations",
                  "planned_unit_cost"]

    def get_planned_unit_cost(self, obj):
        p = services.planned_cost(obj, obj.quantity)
        return {k: str(v / obj.quantity) for k, v in p.items()}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        product = attrs.get("product", getattr(self.instance, "product", None))
        for c in attrs.get("components", []):
            if c["product"] == product:
                raise serializers.ValidationError("A product cannot be a component of itself.")
            if not c["product"].is_stock:
                raise serializers.ValidationError(f"Component {c['product'].sku} must be a stockable product.")
        return attrs

    @transaction.atomic
    def _children(self, bom, comps, ops):
        if comps is not None:
            bom.components.all().delete()
            for c in comps:
                c.pop("id", None)
                BomComponent.objects.create(bom=bom, **c)
        if ops is not None:
            bom.operations.all().delete()
            for o in ops:
                o.pop("id", None)
                BomOperation.objects.create(bom=bom, **o)

    def create(self, validated):
        comps, ops = validated.pop("components", []), validated.pop("operations", [])
        bom = super().create(validated)
        self._children(bom, comps, ops)
        return bom

    def update(self, instance, validated):
        if instance.orders.exclude(state__in=["done", "cancelled"]).exists() and \
                ("components" in validated or "operations" in validated):
            raise BusinessError("Open manufacturing orders use this BOM; create a new BOM version instead.", "in_use")
        comps, ops = validated.pop("components", None), validated.pop("operations", None)
        bom = super().update(instance, validated)
        self._children(bom, comps, ops)
        return bom


class MoComponentSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = MoComponent
        fields = ["id", "product", "product_sku", "product_name", "planned_qty", "consumed_qty", "consumed_value"]


class MoOperationSerializer(serializers.ModelSerializer):
    work_center_code = serializers.CharField(source="work_center.code", read_only=True)

    class Meta:
        model = MoOperation
        fields = ["id", "name", "work_center", "work_center_code", "planned_minutes", "actual_minutes", "labour_cost",
                  "overhead_cost"]


class MoSerializer(CompanyScopedSerializer):
    components = MoComponentSerializer(many=True, read_only=True)
    operations = MoOperationSerializer(many=True, read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    wip_balance = serializers.DecimalField(max_digits=20, decimal_places=4, read_only=True)

    class Meta:
        model = ManufacturingOrder
        fields = ["id", "number", "product", "product_name", "bom", "quantity", "warehouse", "planned_date", "state",
                  "produced_qty", "scrapped_qty", "material_cost", "labour_cost", "overhead_cost", "output_value",
                  "scrap_value", "wip_balance", "date_done", "sales_order", "notes", "components", "operations"]
        read_only_fields = ["number", "state", "produced_qty", "scrapped_qty", "material_cost", "labour_cost",
                            "overhead_cost", "output_value", "scrap_value", "date_done"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance and self.instance.state != "draft":
            raise serializers.ValidationError("Only draft orders can be edited.")
        return attrs


class WorkCenterViewSet(CompanyViewSet):
    queryset = WorkCenter.objects.all()
    serializer_class = WorkCenterSerializer
    perms = {"list": "manufacturing.order.view", "*": "manufacturing.bom.manage"}
    pagination_class = None


class BomViewSet(CompanyViewSet):
    queryset = Bom.objects.select_related("product").prefetch_related("components__product", "operations")
    serializer_class = BomSerializer
    perms = {"list": "manufacturing.order.view", "*": "manufacturing.bom.manage"}
    pagination_class = None


class ManufacturingOrderViewSet(CompanyViewSet):
    queryset = ManufacturingOrder.objects.select_related("product").prefetch_related("components__product",
                                                                                     "operations__work_center")
    serializer_class = MoSerializer
    perms = {"list": "manufacturing.order.view", "costs": "manufacturing.order.view", "wip": "manufacturing.order.view",
             "*": "manufacturing.order.manage"}
    filterset_fields = ["state", "product"]
    search_fields = ["number", "product__sku", "product__name"]

    def perform_destroy(self, instance):
        if instance.state != "draft":
            raise BusinessError("Only drafts can be deleted.", "not_draft")
        instance.delete()

    def _d(self, request):
        return parse_date(request.data.get("date"), timezone.localdate())

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        return Response(self.get_serializer(services.confirm(self.get_object(), request.user)).data)

    @action(detail=True, methods=["post"])
    def consume(self, request, pk=None):
        services.consume(self.get_object(), request.user, request.data.get("quantities"), self._d(request))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="record-operations")
    def record_operations(self, request, pk=None):
        services.record_operations(self.get_object(), request.user, request.data.get("minutes", {}), self._d(request))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"])
    def produce(self, request, pk=None):
        services.produce(self.get_object(), request.user, request.data.get("quantity") or 0,
                         request.data.get("scrap_quantity") or 0, self._d(request), bool(request.data.get("finish")))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return Response(self.get_serializer(services.cancel(self.get_object(), request.user)).data)

    @action(detail=True)
    def costs(self, request, pk=None):
        return Response(services.cost_report(self.get_object()))

    @action(detail=False)
    def wip(self, request):
        return Response(services.wip_report(self.company))
