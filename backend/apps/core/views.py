from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.iam.access import ErpPermission, resolve_company

from .api import CompanyReadOnlyViewSet, CompanyViewSet
from .models import AuditLog, Company, CostCenter, Currency, ExchangeRate
from .serializers import (AuditLogSerializer, CompanySerializer, CostCenterSerializer, CurrencySerializer,
                          ExchangeRateSerializer)


class CurrencyViewSet(viewsets.ModelViewSet):
    queryset = Currency.objects.all()
    serializer_class = CurrencySerializer
    permission_classes = [ErpPermission]
    perms = {"list": "authenticated", "*": "core.currency.manage"}
    http_method_names = ["get", "post", "patch", "head", "options"]
    pagination_class = None


class CompanyViewSetCurrent(mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """The current company (from X-Company)."""
    serializer_class = CompanySerializer
    permission_classes = [ErpPermission]
    perms = {"list": "core.company.view", "current": ["core.company.view", "ledger.account.view"],
             "update": "core.company.manage"}

    def get_object(self):
        return resolve_company(self.request)

    @action(detail=False, methods=["get", "patch"])
    def current(self, request):
        company = self.get_object()
        if request.method == "PATCH":
            if "core.company.manage" not in request.user.permissions_for(company.pk):
                return Response({"detail": "Missing permission: core.company.manage"}, status=403)
            s = self.get_serializer(company, data=request.data, partial=True)
            s.is_valid(raise_exception=True)
            s.save()
            return Response(s.data)
        return Response(self.get_serializer(company).data)


class ExchangeRateViewSet(CompanyViewSet):
    queryset = ExchangeRate.objects.all()
    serializer_class = ExchangeRateSerializer
    perms = {"list": "ledger.account.view", "*": "core.currency.manage"}
    filterset_fields = ["currency", "date"]


class CostCenterViewSet(CompanyViewSet):
    queryset = CostCenter.objects.all()
    serializer_class = CostCenterSerializer
    perms = {"list": "ledger.account.view", "*": "core.costcenter.manage"}
    search_fields = ["code", "name"]
    pagination_class = None


class AuditLogViewSet(CompanyReadOnlyViewSet):
    queryset = AuditLog.objects.select_related("user")
    serializer_class = AuditLogSerializer
    perms = {"list": "audit.log.view", "verify": "audit.log.view"}
    filterset_fields = ["action", "object_type", "object_id", "user"]
    search_fields = ["object_repr", "actor_label", "action"]

    @action(detail=False, methods=["get"])
    def verify(self, request):
        ok, broken = AuditLog.verify_chain()
        return Response({"intact": ok, "first_broken_id": broken, "entries": AuditLog.objects.count()})
