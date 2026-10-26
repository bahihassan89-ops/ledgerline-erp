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
from .models import (Bom, BomByproduct, BomComponent, BomOperation, ManufacturingOrder, MoByproduct, MoComponent,
                     MoOperation, QualityInspection, WorkCenter, WorkCenterCalendarException)


class WorkCenterSerializer(CompanyScopedSerializer):
    class Meta:
        model = WorkCenter
        fields = ["id", "code", "name", "labour_rate", "overhead_rate", "cost_center", "capacity_minutes_per_day",
                  "working_weekdays", "is_subcontracted", "default_vendor", "active"]


class WorkCenterCalendarExceptionSerializer(CompanyScopedSerializer):
    work_center_code = serializers.CharField(source="work_center.code", read_only=True)

    class Meta:
        model = WorkCenterCalendarException
        fields = ["id", "work_center", "work_center_code", "date", "available_minutes", "notes"]


class BomComponentSerializer(CompanyScopedSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)

    class Meta:
        model = BomComponent
        fields = ["id", "product", "product_sku", "quantity", "scrap_pct"]


class BomOperationSerializer(CompanyScopedSerializer):
    class Meta:
        model = BomOperation
        fields = ["id", "sequence", "name", "work_center", "minutes", "alternate_work_centers"]


class BomByproductSerializer(CompanyScopedSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)

    class Meta:
        model = BomByproduct
        fields = ["id", "product", "product_sku", "quantity", "unit_value"]


class BomSerializer(CompanyScopedSerializer):
    components = BomComponentSerializer(many=True)
    operations = BomOperationSerializer(many=True, required=False)
    byproducts = BomByproductSerializer(many=True, required=False)
    product_name = serializers.CharField(source="product.name", read_only=True)
    planned_unit_cost = serializers.SerializerMethodField()

    class Meta:
        model = Bom
        fields = ["id", "code", "version", "revises", "product", "product_name", "quantity", "active", "backflush",
                  "notes", "components", "operations", "byproducts", "planned_unit_cost"]
        read_only_fields = ["version", "revises"]

    def get_planned_unit_cost(self, obj):
        p = services.planned_cost(obj, obj.quantity)
        return {k: str(v / obj.quantity) for k, v in p.items()}

    def _descendant_product_ids(self, product, seen=None):
        """Every product reachable by following active sub-assembly BOMs down from `product` --
        used to reject a component that would close a cycle (direct or through several levels)."""
        seen = seen or set()
        if product.pk in seen:
            return seen
        seen = seen | {product.pk}
        for sub_bom in product.boms.filter(active=True):
            for c in sub_bom.components.select_related("product"):
                seen |= self._descendant_product_ids(c.product, seen)
        return seen

    def validate(self, attrs):
        attrs = super().validate(attrs)
        product = attrs.get("product", getattr(self.instance, "product", None))
        for c in attrs.get("components", []):
            if c["product"] == product:
                raise serializers.ValidationError("A product cannot be a component of itself.")
            if not c["product"].is_stock:
                raise serializers.ValidationError(f"Component {c['product'].sku} must be a stockable product.")
            if product and product.pk in self._descendant_product_ids(c["product"]):
                raise serializers.ValidationError(
                    f"Using {c['product'].sku} here would create a circular BOM: it is (directly or through "
                    f"a sub-assembly) built from {product.sku}.")
        return attrs

    @transaction.atomic
    def _children(self, bom, comps, ops, byprods):
        if comps is not None:
            bom.components.all().delete()
            for c in comps:
                c.pop("id", None)
                BomComponent.objects.create(bom=bom, **c)
        if ops is not None:
            bom.operations.all().delete()
            for o in ops:
                o.pop("id", None)
                alts = o.pop("alternate_work_centers", None)
                op = BomOperation.objects.create(bom=bom, **o)
                if alts:
                    op.alternate_work_centers.set(alts)
        if byprods is not None:
            bom.byproducts.all().delete()
            for b in byprods:
                b.pop("id", None)
                BomByproduct.objects.create(bom=bom, **b)

    def create(self, validated):
        comps = validated.pop("components", [])
        ops = validated.pop("operations", [])
        byprods = validated.pop("byproducts", [])
        bom = super().create(validated)
        self._children(bom, comps, ops, byprods)
        return bom

    def update(self, instance, validated):
        if instance.orders.exclude(state__in=["done", "cancelled"]).exists() and \
                ("components" in validated or "operations" in validated or "byproducts" in validated):
            raise BusinessError("Open manufacturing orders use this BOM; create a new BOM version instead.", "in_use")
        comps = validated.pop("components", None)
        ops = validated.pop("operations", None)
        byprods = validated.pop("byproducts", None)
        bom = super().update(instance, validated)
        self._children(bom, comps, ops, byprods)
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
        fields = ["id", "name", "work_center", "work_center_code", "alternate_work_centers", "planned_minutes",
                  "actual_minutes", "labour_cost", "overhead_cost", "subcontract_cost", "subcontract_billed",
                  "scheduled_start", "scheduled_end"]
        read_only_fields = ["subcontract_billed"]


class MoByproductSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)

    class Meta:
        model = MoByproduct
        fields = ["id", "product", "product_sku", "planned_qty", "produced_qty", "value"]


class QualityInspectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = QualityInspection
        fields = ["id", "mo", "qty_passed", "qty_failed", "qty_used", "notes", "inspected_at"]
        read_only_fields = ["mo", "qty_used"]


class MoSerializer(CompanyScopedSerializer):
    components = MoComponentSerializer(many=True, read_only=True)
    operations = MoOperationSerializer(many=True, read_only=True)
    byproducts = MoByproductSerializer(many=True, read_only=True)
    quality_inspections = QualityInspectionSerializer(many=True, read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    wip_balance = serializers.DecimalField(max_digits=20, decimal_places=4, read_only=True)

    class Meta:
        model = ManufacturingOrder
        fields = ["id", "number", "product", "product_name", "bom", "quantity", "warehouse", "planned_date", "state",
                  "produced_qty", "scrapped_qty", "material_cost", "labour_cost", "overhead_cost", "output_value",
                  "scrap_value", "byproduct_value", "wip_balance", "date_done", "sales_order", "notes", "components",
                  "operations", "byproducts", "quality_inspections"]
        read_only_fields = ["number", "state", "produced_qty", "scrapped_qty", "material_cost", "labour_cost",
                            "overhead_cost", "output_value", "scrap_value", "byproduct_value", "date_done"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance and self.instance.state != "draft":
            raise serializers.ValidationError("Only draft orders can be edited.")
        return attrs


class WorkCenterViewSet(CompanyViewSet):
    queryset = WorkCenter.objects.all()
    serializer_class = WorkCenterSerializer
    perms = {"list": "manufacturing.order.view", "load": "manufacturing.order.view", "*": "manufacturing.bom.manage"}
    pagination_class = None

    @action(detail=True)
    def load(self, request, pk=None):
        """Open operations currently scheduled at this work center, for a quick look at how booked up
        it is -- see services.schedule_operations for how scheduled_start/scheduled_end are set."""
        wc = self.get_object()
        ops = (MoOperation.objects.filter(work_center=wc, scheduled_start__isnull=False, mo__company=self.company)
               .exclude(mo__state__in=["done", "cancelled"]).select_related("mo").order_by("scheduled_start", "id"))
        rows = [{"mo_number": op.mo.number, "operation": op.name, "scheduled_start": str(op.scheduled_start),
                "scheduled_end": str(op.scheduled_end), "planned_minutes": str(op.planned_minutes)} for op in ops]
        return Response({"work_center": wc.code, "operations": rows})


class WorkCenterCalendarExceptionViewSet(CompanyViewSet):
    queryset = WorkCenterCalendarException.objects.select_related("work_center")
    serializer_class = WorkCenterCalendarExceptionSerializer
    perms = {"list": "manufacturing.order.view", "*": "manufacturing.bom.manage"}
    pagination_class = None
    filterset_fields = ["work_center", "date"]


class BomViewSet(CompanyViewSet):
    queryset = Bom.objects.select_related("product").prefetch_related("components__product", "operations")
    serializer_class = BomSerializer
    perms = {"list": "manufacturing.order.view", "revise": "manufacturing.bom.manage", "*": "manufacturing.bom.manage"}
    pagination_class = None
    filterset_fields = ["code", "product", "active"]

    @action(detail=True, methods=["post"])
    def revise(self, request, pk=None):
        """Start a new version of this BOM (see services.revise_bom): always allowed, even while open
        manufacturing orders use the current version -- unlike editing components/operations in place."""
        new = services.revise_bom(self.get_object(), request.user)
        return Response(self.get_serializer(new).data, status=201)


class ManufacturingOrderViewSet(CompanyViewSet):
    queryset = ManufacturingOrder.objects.select_related("product").prefetch_related("components__product",
                                                                                     "operations__work_center")
    serializer_class = MoSerializer
    perms = {"list": "manufacturing.order.view", "costs": "manufacturing.order.view", "wip": "manufacturing.order.view",
             "bill_subcontract": "billing.bill.create", "*": "manufacturing.order.manage"}
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
        services.consume(self.get_object(), request.user, request.data.get("quantities"), self._d(request),
                         request.data.get("lots"), request.data.get("serials"))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="record-operations")
    def record_operations(self, request, pk=None):
        services.record_operations(self.get_object(), request.user, request.data.get("minutes", {}), self._d(request))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"])
    def reschedule(self, request, pk=None):
        """Re-run the finite-capacity scheduler (see services.schedule_operations), e.g. after editing
        a BOM operation's work center or another order's schedule freed up capacity."""
        return Response(self.get_serializer(services.reschedule(self.get_object())).data)

    @action(detail=True, methods=["post"])
    def subcontract(self, request, pk=None):
        """Book a vendor's invoiced charge for an operation at a subcontracted work center into WIP."""
        services.record_subcontract_cost(self.get_object(), request.user, request.data.get("operation"),
                                         request.data.get("amount") or 0, self._d(request))
        return Response(self.get_serializer(self.get_object()).data, status=201)

    @action(detail=True, methods=["post"], url_path="bill-subcontract")
    def bill_subcontract(self, request, pk=None):
        """Draft a vendor bill for a subcontracted operation's charge (see
        services.create_subcontract_bill) -- cannot exceed what record_subcontract_cost already
        charged to WIP for that operation."""
        from apps.billing.serializers import InvoiceSerializer
        bill = services.create_subcontract_bill(self.get_object(), request.user, request.data.get("operation"),
                                                 request.data.get("amount") or 0, self._d(request),
                                                 request.data.get("vendor_reference", ""),
                                                 request.data.get("tax"))
        return Response(InvoiceSerializer(bill, context=self.get_serializer_context()).data, status=201)

    @action(detail=True, methods=["post"])
    def inspect(self, request, pk=None):
        """Record a quality-control inspection of finished output (see services.inspect) -- required
        before produce() can release output of a `requires_qc` product."""
        qi = services.inspect(self.get_object(), request.user, request.data.get("qty_passed") or 0,
                              request.data.get("qty_failed") or 0, request.data.get("notes", ""), self._d(request))
        return Response(QualityInspectionSerializer(qi).data, status=201)

    @action(detail=True, methods=["post"])
    def produce(self, request, pk=None):
        services.produce(self.get_object(), request.user, request.data.get("quantity") or 0,
                         request.data.get("scrap_quantity") or 0, self._d(request), bool(request.data.get("finish")),
                         request.data.get("lot_number"), request.data.get("serials"), request.data.get("byproducts"))
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
