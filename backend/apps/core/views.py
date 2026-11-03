import json
import os
import re
import time
import uuid

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.access import ErpPermission, GlobalPermission, resolve_company

from . import services
from .api import CompanyReadOnlyViewSet, CompanyViewSet
from .audit import record as audit_record
from .exceptions import BusinessError
from .models import (AuditLog, Branch, Company, CompanyModule, CompanyState, CostCenter, Currency, ExchangeRate,
                     Notification, PushSubscription)
from .modules import MODULE_REGISTRY
from .serializers import (AuditLogSerializer, BranchSerializer, CompanyDraftCreateSerializer, CompanyLiteSerializer,
                          CompanyModuleSerializer, CompanyProvisioningSerializer, CompanySerializer,
                          CostCenterSerializer, CurrencySerializer, ExchangeRateSerializer, NotificationSerializer,
                          PushSubscriptionSerializer, RejectCompanySerializer)


class CurrencyViewSet(viewsets.ModelViewSet):
    queryset = Currency.objects.all()
    serializer_class = CurrencySerializer
    permission_classes = [ErpPermission]
    perms = {"list": "authenticated", "*": "core.currency.manage"}
    http_method_names = ["get", "post", "patch", "head", "options"]
    pagination_class = None


class CompanyLiteViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Read-only list of every active company in the install (id/code/name only, no financial
    data) -- not scoped by X-Company, since by definition it needs to show companies other than
    the caller's own. Used by the partner form to pick Partner.linked_company when marking a
    partner as intercompany."""
    queryset = Company.objects.filter(state=CompanyState.ACTIVE).order_by("name")
    serializer_class = CompanyLiteSerializer
    permission_classes = [ErpPermission]
    perms = {"list": "authenticated"}
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


_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _cell_value(v):
    """Turn a JSON value from a report row into a genuine spreadsheet cell value -- numeric-looking
    strings (every amount/qty in our reports is serialised as a decimal string, e.g. "1234.5600")
    become real numeric cells Excel can sum/format, instead of text, which is the whole point of
    exporting to .xlsx rather than .csv."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s and _NUMERIC_RE.match(s):
            n = float(s)
            return int(n) if n.is_integer() and abs(n) < 1e15 else n
        return v
    return str(v)


class ExportXlsxView(APIView):
    """Generic 'turn these columns + rows into a real .xlsx file' endpoint, shared by every
    report and register in the app (Financial Reports, cheque/guarantee-cheque/instalment-plan/
    consignment registers, etc.) so none of them need bespoke export code -- the client already
    builds a cols/rows pair for its existing CSV export (see lib/format.ts toCSV); this just
    renders the same shape as a genuine spreadsheet instead of plain text. No company-scoped data
    is read here -- the client sends exactly the rows it already fetched and rendered."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        body = request.data or {}
        filename = str(body.get("filename") or "export.xlsx").strip() or "export.xlsx"
        if not filename.lower().endswith(".xlsx"):
            filename += ".xlsx"
        sheets = body.get("sheets") or [{"name": body.get("sheet_name", "Sheet1"),
                                         "cols": body.get("cols", []), "rows": body.get("rows", [])}]
        wb = Workbook()
        wb.remove(wb.active)
        used_names = set()
        for sheet in sheets[:50]:
            name = str(sheet.get("name") or "Sheet1").strip()[:31] or "Sheet1"
            base, i = name, 2
            while name in used_names:
                name = f"{base[:28]} {i}"
                i += 1
            used_names.add(name)
            ws = wb.create_sheet(name)
            cols = sheet.get("cols") or []
            rows = sheet.get("rows") or []
            ws.append([str(c.get("title") or c.get("key") or "") for c in cols])
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for row in rows[:100000]:
                ws.append([_cell_value(row.get(c.get("key"))) for c in cols])
            for i, c in enumerate(cols, start=1):
                ws.column_dimensions[get_column_letter(i)].width = min(max(len(str(c.get("title") or "")) + 2, 10), 40)
            if rows:
                ws.freeze_panes = "A2"
        if not wb.sheetnames:
            wb.create_sheet("Sheet1")
        resp = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'
        wb.save(resp)
        return resp


# A request older than this is treated as abandoned (e.g. the backup container was restarted
# mid-run) so a stuck "in progress" state never permanently blocks the button.
BACKUP_REQUEST_STALE_SECONDS = 600


class BackupView(APIView):
    """On-demand database + media backup for the Central Financial Controller / CEO, so they are
    not stuck waiting for the daily schedule (see deploy/backup/backup.sh, BACKUP_INTERVAL_HOURS).

    This process never touches the database credentials, the GPG passphrase, or the encrypted
    backup files -- all of that stays inside the separate `backup` container (least privilege,
    same reasoning as erp_app being DML-only). A click here just drops a small request file on a
    shared control volume; the backup container notices it within a few seconds, runs the backup,
    and writes the result back to the same volume. There is no cloud destination wired up yet
    (nothing here has gone live to a cloud host) -- this is the on-demand trigger + status piece,
    deliberately left generic (see backup() in backup.sh) so pushing the resulting file to S3 /
    GCS / Backblaze / rclone later is a small addition to that function, not a redesign."""
    permission_classes = [GlobalPermission]
    global_perms = {"get": "ops.backup.manage", "post": "ops.backup.manage"}

    def get(self, request):
        return Response(_backup_status())

    def post(self, request):
        ctl = settings.BACKUP_CTL_DIR
        os.makedirs(ctl, exist_ok=True)
        now = time.time()
        for name in os.listdir(ctl):
            if name.startswith("request-"):
                path = os.path.join(ctl, name)
                if now - os.path.getmtime(path) < BACKUP_REQUEST_STALE_SECONDS:
                    return Response({"detail": "A backup is already in progress."}, status=409)
                os.remove(path)  # stale -- the backup worker never picked it up; allow a fresh one
        req_id = uuid.uuid4().hex[:12]
        payload = {"id": req_id, "requested_by": request.user.get_username(),
                   "requested_at": timezone.now().isoformat()}
        tmp = os.path.join(ctl, f".request-{req_id}.tmp")
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        os.rename(tmp, os.path.join(ctl, f"request-{req_id}.json"))  # atomic publish
        audit_record("backup.requested", object_type="backup", object_id=req_id, changes=payload)
        return Response({"detail": "Backup requested.", **payload}, status=202)


def _backup_status():
    ctl = settings.BACKUP_CTL_DIR
    status = {"last_backup_at": None, "last_status": None, "last_trigger": None,
              "requested_by": None, "last_size_kb": None, "pending": False}
    if os.path.isdir(ctl):
        status["pending"] = any(n.startswith("request-") for n in os.listdir(ctl))
        path = os.path.join(ctl, "status.json")
        if os.path.exists(path):
            try:
                with open(path) as fh:
                    status.update(json.load(fh))
            except (OSError, ValueError):
                pass
    return status
