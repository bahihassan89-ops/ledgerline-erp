from rest_framework.decorators import action
from rest_framework.response import Response

from apps.billing.serializers import InvoiceSerializer
from apps.core.api import CompanyViewSet
from apps.core.exceptions import BusinessError
from apps.inventory.serializers import StockMoveSerializer
from apps.ledger.views import parse_date

from . import services
from .models import SalesOrder
from .serializers import SalesOrderSerializer


class SalesOrderViewSet(CompanyViewSet):
    queryset = SalesOrder.objects.select_related("partner").prefetch_related("lines__product")
    serializer_class = SalesOrderSerializer
    perms = {"list": "sales.order.view", "create": "sales.order.create", "update": "sales.order.create",
             "destroy": "sales.order.create", "confirm": "sales.order.confirm", "deliver": "inventory.stock.move",
             "create_invoice": "billing.invoice.create", "cancel": "sales.order.cancel"}
    filterset_fields = {"state": ["exact"], "partner": ["exact"], "date": ["gte", "lte"]}
    search_fields = ["number", "partner__name", "customer_reference"]

    def perform_destroy(self, instance):
        if instance.state != "draft":
            raise BusinessError("Only quotations can be deleted.", "not_draft")
        instance.delete()

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        o = services.confirm(self.get_object(), request.user)
        return Response(self.get_serializer(o).data, status=202 if o.state == "to_approve" else 200)

    @action(detail=True, methods=["post"])
    def deliver(self, request, pk=None):
        moves = services.deliver(self.get_object(), request.user, request.data.get("quantities"),
                                 parse_date(request.data.get("date")))
        return Response({"moves": StockMoveSerializer(moves, many=True).data,
                         "order": self.get_serializer(self.get_object()).data})

    @action(detail=True, methods=["post"], url_path="create-invoice")
    def create_invoice(self, request, pk=None):
        inv = services.create_invoice(self.get_object(), request.user, parse_date(request.data.get("date")))
        return Response(InvoiceSerializer(inv, context=self.get_serializer_context()).data, status=201)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return Response(self.get_serializer(services.cancel(self.get_object(), request.user,
                                                            request.data.get("reason", ""))).data)
