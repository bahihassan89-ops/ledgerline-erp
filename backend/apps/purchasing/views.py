from rest_framework.decorators import action
from rest_framework.response import Response

from apps.billing.serializers import InvoiceSerializer
from apps.core.api import CompanyViewSet
from apps.core.exceptions import BusinessError
from apps.inventory.serializers import StockMoveSerializer
from apps.ledger.views import parse_date

from . import services
from .models import PurchaseOrder
from .serializers import PurchaseOrderSerializer


class PurchaseOrderViewSet(CompanyViewSet):
    queryset = PurchaseOrder.objects.select_related("partner").prefetch_related("lines__product")
    serializer_class = PurchaseOrderSerializer
    perms = {"list": "purchasing.order.view", "create": "purchasing.order.create",
             "update": "purchasing.order.create", "destroy": "purchasing.order.create",
             "confirm": "purchasing.order.confirm", "receive": "inventory.stock.move",
             "create_bill": "billing.bill.create", "cancel": "purchasing.order.cancel"}
    filterset_fields = {"state": ["exact"], "partner": ["exact"], "date": ["gte", "lte"]}
    search_fields = ["number", "partner__name", "vendor_reference"]

    def perform_destroy(self, instance):
        if instance.state != "draft":
            raise BusinessError("Only RFQs can be deleted.", "not_draft")
        instance.delete()

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        o = services.confirm(self.get_object(), request.user)
        return Response(self.get_serializer(o).data, status=202 if o.state == "to_approve" else 200)

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        moves = services.receive(self.get_object(), request.user, request.data.get("quantities"),
                                 parse_date(request.data.get("date")))
        return Response({"moves": StockMoveSerializer(moves, many=True).data,
                         "order": self.get_serializer(self.get_object()).data})

    @action(detail=True, methods=["post"], url_path="create-bill")
    def create_bill(self, request, pk=None):
        bill = services.create_bill(self.get_object(), request.user, parse_date(request.data.get("date")),
                                    request.data.get("vendor_reference", ""))
        return Response(InvoiceSerializer(bill, context=self.get_serializer_context()).data, status=201)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return Response(self.get_serializer(services.cancel(self.get_object(), request.user,
                                                            request.data.get("reason", ""))).data)
