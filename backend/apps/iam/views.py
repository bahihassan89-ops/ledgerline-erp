from rest_framework import exceptions, mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import audit
from apps.core.api import CompanyViewSet
from apps.core.exceptions import BusinessError

from . import presence
from .access import ErpPermission, GlobalPermission, resolve_company
from .models import GlobalRole, PresenceEvent, Role, User, UserPresence
from .permissions_catalog import CATALOG, GLOBAL_CATALOG
from .serializers import (GlobalRoleSerializer, PresenceEventSerializer, RoleSerializer, UserPresenceSerializer,
                          UserSerializer)


class RoleViewSet(CompanyViewSet):
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    perms = {"list": "iam.user.view", "*": "iam.role.manage"}
    pagination_class = None

    def perform_create(self, serializer):
        serializer.save(company=self.company)

    def perform_destroy(self, instance):
        if instance.membership_set.exists():
            raise BusinessError("Role is assigned to users; deactivate it instead.", "in_use")
        instance.delete()


class UserViewSet(viewsets.ModelViewSet):
    serializer_class = UserSerializer
    permission_classes = [ErpPermission]
    perms = {"list": "iam.user.view", "*": "iam.user.manage"}
    http_method_names = ["get", "post", "patch", "head", "options"]
    search_fields = ["username", "email", "first_name", "last_name"]

    def get_queryset(self):
        company = resolve_company(self.request)
        return User.objects.filter(memberships__company=company).distinct().order_by("username")

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "company": resolve_company(self.request)}

    @action(detail=True, methods=["post"])
    def unlock(self, request, pk=None):
        u = self.get_object()
        u.locked_until = None
        u.failed_login_count = 0
        u.save(update_fields=["locked_until", "failed_login_count"])
        audit.record("unlock", u)
        return Response({"ok": True})

    @action(detail=True, methods=["post"])
    def reset_mfa(self, request, pk=None):
        u = self.get_object()
        if u.pk == request.user.pk:
            raise BusinessError("You cannot reset your own MFA.", "self_action")
        u.mfa_enabled = False
        u.mfa_secret = ""
        u.mfa_last_counter = 0
        u.save(update_fields=["mfa_enabled", "mfa_secret", "mfa_last_counter"])
        audit.record("mfa_reset", u)
        return Response({"ok": True})

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        u = self.get_object()
        if u.pk == request.user.pk:
            raise BusinessError("You cannot deactivate yourself.", "self_action")
        m = u.membership(resolve_company(request).pk)
        if m:
            m.active = False
            m.save(update_fields=["active"])
        audit.record("deactivate_membership", u)
        return Response({"ok": True})


class PermissionCatalogView(APIView):
    permission_classes = [ErpPermission]
    perms = {"get": "iam.user.view"}

    def get(self, request):
        return Response([{"code": k, "description": v, "module": k.split(".")[0]} for k, v in CATALOG.items()])


class GlobalPermissionCatalogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response([{"code": k, "description": v} for k, v in GLOBAL_CATALOG.items()])


class GlobalRoleViewSet(viewsets.ModelViewSet):
    """Defines the global role *bundles* (CEO, Central Controller, Central Accounting Manager).
    Mutating these is CEO-only (company.activate): anyone holding membership.grant_any_company
    could otherwise edit the CEO bundle's own permissions and escalate to it."""
    queryset = GlobalRole.objects.all()
    serializer_class = GlobalRoleSerializer
    permission_classes = [GlobalPermission]
    global_perms = {"list": "membership.grant_any_company", "retrieve": "membership.grant_any_company",
                    "*": "company.activate"}
    pagination_class = None


class HeartbeatView(APIView):
    """Called every ~30s by the frontend while a tab is open. Updates the live 'who's online'
    cache and, on a meaningful change, appends to the permanent PresenceEvent log."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            company = resolve_company(request)
        except exceptions.PermissionDenied:
            company = None
        pres = presence.heartbeat(request.user, company, request.data.get("module_code", ""),
                                  request.data.get("section", ""))
        return Response(UserPresenceSerializer(pres).data)


class TeamActivityViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Central 'who's online, where, doing what' view for the Controller/Accounting Manager/CEO
    team-activity portal."""
    queryset = UserPresence.objects.select_related("user", "company").order_by("user__username")
    serializer_class = UserPresenceSerializer
    permission_classes = [GlobalPermission]
    global_perms = {"list": "presence.view_team", "retrieve": "presence.view_team",
                    "history": "presence.view_team"}
    lookup_field = "user_id"
    pagination_class = None

    @action(detail=True, methods=["get"])
    def history(self, request, user_id=None):
        """The permanent, append-only historical log for one user (see PresenceEvent)."""
        events = PresenceEvent.objects.filter(user_id=user_id).select_related("company")[:500]
        return Response(PresenceEventSerializer(events, many=True).data)
