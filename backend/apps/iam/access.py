"""Company resolution and permission checks used by every API view."""
from rest_framework import exceptions, permissions

from apps.core import context
from apps.core.models import Company


def resolve_company(request):
    """Return the Company for this request, verifying the caller belongs to it.

    Users send `X-Company: <id>`. API keys are bound to exactly one company."""
    if hasattr(request, "_erp_company"):
        return request._erp_company
    api_key = getattr(request, "api_key", None)
    if api_key is not None:
        company = api_key.company
    else:
        raw = request.headers.get("X-Company")
        if not raw:
            m = request.user.memberships.filter(active=True, is_default=True).first() or \
                request.user.memberships.filter(active=True).first()
            if not m:
                raise exceptions.PermissionDenied("You do not belong to any company.")
            company = m.company
        else:
            try:
                company = Company.objects.get(pk=int(raw), active=True)
            except (ValueError, Company.DoesNotExist):
                raise exceptions.PermissionDenied("Unknown company.")
            if not request.user.is_superuser and not request.user.membership(company.pk):
                raise exceptions.PermissionDenied("You do not have access to this company.")
    request._erp_company = company
    ctx = context.get()
    ctx.company_id = company.pk
    return company


def effective_permissions(request) -> set:
    if hasattr(request, "_erp_perms"):
        return request._erp_perms
    company = resolve_company(request)
    perms = request.user.permissions_for(company.pk)
    api_key = getattr(request, "api_key", None)
    if api_key is not None:
        # An API key can never exceed its owner's rights.
        perms = perms & set(api_key.scopes)
    request._erp_perms = perms
    return perms


def has_perm(request, code) -> bool:
    return code in effective_permissions(request)


def require(request, code):
    if not has_perm(request, code):
        raise exceptions.PermissionDenied(f"Missing permission: {code}")


class ErpPermission(permissions.BasePermission):
    """Views declare `perms = {"list": "x.view", "create": "x.create", ...}`.
    Actions not declared fall back to `perms["*"]`; if nothing matches, access is denied
    (secure by default)."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        ctx = context.get()
        ctx.user = request.user
        ctx.actor_label = getattr(request, "api_key", None) and f"apikey:{request.api_key.name}" or \
            request.user.get_username()
        mapping = getattr(view, "perms", None) or {}
        action = getattr(view, "action", None) or request.method.lower()
        if action in ("partial_update",) and action not in mapping:
            action = "update"
        if action == "retrieve" and action not in mapping:
            action = "list"
        code = mapping.get(action, mapping.get("*"))
        if code is None:
            return False
        if code == "authenticated":
            resolve_company(request)
            return True
        codes = code if isinstance(code, (list, tuple)) else [code]
        perms = effective_permissions(request)
        if not any(c in perms for c in codes):
            raise exceptions.PermissionDenied(f"Missing permission: {' or '.join(codes)}")
        return True
