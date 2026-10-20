from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import audit
from apps.core.api import CompanyViewSet
from apps.core.exceptions import BusinessError

from .access import ErpPermission, resolve_company
from .models import Role, User
from .permissions_catalog import CATALOG
from .serializers import RoleSerializer, UserSerializer


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
