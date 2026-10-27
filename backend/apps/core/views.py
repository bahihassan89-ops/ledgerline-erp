from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.iam.access import ErpPermission, GlobalPermission, resolve_company

from . import services
from .api import CompanyReadOnlyViewSet, CompanyViewSet
from .exceptions import BusinessError
from .models import (AuditLog, Branch, Company, CompanyModule, CompanyState, CostCenter, Currency, ExchangeRate,
                     Notification, PushSubscription)
from .modules import MODULE_REGISTRY
from .serializers import (AuditLogSerializer, BranchSerializer, CompanyDraftCreateSerializer, CompanyModuleSerializer,
                          CompanyProvisioningSerializer, CompanySerializer, CostCenterSerializer, CurrencySerializer,
                          ExchangeRateSerializer, NotificationSerializer, PushSubscriptionSerializer,
                          RejectCompanySerializer)


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


class BranchViewSet(CompanyViewSet):
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer
    perms = {"list": "ledger.account.view", "*": "core.branch.manage"}
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
        # Chains are per-company (see AuditLog.save()); verify this tenant's chain, not
        # every company's in the whole system.
        ok, broken = AuditLog.verify_chain(company_id=self.company.pk)
        return Response({"intact": ok, "first_broken_id": broken,
                         "entries": AuditLog.objects.filter(company=self.company).count()})


class CompanyProvisioningViewSet(viewsets.ModelViewSet):
    """Draft -> pending_approval -> active company provisioning. Not a CompanyViewSet: there is
    no X-Company context yet (the company being configured isn't active), so access is governed
    entirely by global permissions, not per-company membership."""
    serializer_class = CompanyProvisioningSerializer
    permission_classes = [GlobalPermission]
    global_perms = {"list": "company.create", "retrieve": "company.create", "create": "company.create",
                    "update": "company.create", "partial_update": "company.create",
                    "module_registry": "company.create",
                    "modules": "company.module.manage", "submit": "company.submit"}
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return Company.objects.exclude(state=CompanyState.ACTIVE).order_by("-created_at")

    def create(self, request, *a, **kw):
        s = CompanyDraftCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        company = services.create_draft_company(request.user, **s.validated_data)
        return Response(self.get_serializer(company).data, status=201)

    def perform_update(self, serializer):
        if serializer.instance.state not in (CompanyState.DRAFT, CompanyState.REJECTED):
            raise BusinessError("Only a draft or rejected company can be edited.", "wrong_state")
        serializer.save()

    @action(detail=True, methods=["get", "post"])
    def modules(self, request, pk=None):
        company = self.get_object()
        if request.method == "POST":
            services.set_modules(company, request.data.get("module_codes", []))
        return Response(CompanyModuleSerializer(
            CompanyModule.objects.filter(company=company).order_by("module_code"), many=True).data)

    @action(detail=False, methods=["get"], url_path="module-registry")
    def module_registry(self, request):
        return Response([{"code": k, "description": v} for k, v in MODULE_REGISTRY.items()])

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        company = services.submit_company(self.get_object(), request.user)
        return Response(self.get_serializer(company).data)


class CompanyApprovalViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """The CEO's approval queue: companies a Controller has fully configured and submitted,
    waiting for final sign-off before they become usable (Company.active=True)."""
    serializer_class = CompanyProvisioningSerializer
    permission_classes = [GlobalPermission]
    global_perms = {"list": "company.activate", "retrieve": "company.activate",
                    "approve": "company.activate", "reject": "company.reject"}

    def get_queryset(self):
        return Company.objects.filter(state=CompanyState.PENDING_APPROVAL).order_by("submitted_at")

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        company = services.approve_company(self.get_object(), request.user)
        return Response(self.get_serializer(company).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        s = RejectCompanySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        company = services.reject_company(self.get_object(), request.user, s.validated_data["reason"])
        return Response(self.get_serializer(company).data)


class NotificationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """A user's own activity feed, across every company they belong to -- not a CompanyViewSet:
    the bell icon should show approvals waiting anywhere, not just in the currently-selected
    company."""
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return Response({"count": self.get_queryset().filter(read_at__isnull=True).count()})

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        self.get_queryset().filter(read_at__isnull=True).update(read_at=timezone.now())
        return Response({"ok": True})

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notif = self.get_object()
        if not notif.read_at:
            notif.read_at = timezone.now()
            notif.save(update_fields=["read_at"])
        return Response(self.get_serializer(notif).data)


class PushSubscriptionViewSet(mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Registers/unregisters this browser/phone for Web Push. One row per endpoint; re-subscribing
    the same endpoint (e.g. after the VAPID key check runs again) just updates the keys."""
    serializer_class = PushSubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PushSubscription.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        sub, _ = PushSubscription.objects.update_or_create(
            endpoint=serializer.validated_data["endpoint"], user=self.request.user,
            defaults={"p256dh": serializer.validated_data["p256dh"], "auth": serializer.validated_data["auth"]},
        )
        serializer.instance = sub
