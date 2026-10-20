"""Supporting-document attachments, linked to any business record of the caller's company."""
import hashlib
import mimetypes

from django.apps import apps as django_apps
from django.conf import settings
from django.http import FileResponse
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from apps.core import audit
from apps.core.exceptions import BusinessError
from apps.iam.access import ErpPermission, effective_permissions, resolve_company

from .models import Attachment

# object_type -> (model, view permission(s), edit permission(s), "is the record final?")
TYPES = {
    "billing.invoice": ("billing.Invoice", ["billing.invoice.view", "billing.bill.view"],
                        ["billing.invoice.create", "billing.bill.create"], lambda o: o.state == "posted"),
    "billing.payment": ("billing.Payment", ["billing.payment.view"], ["billing.payment.create"],
                        lambda o: o.state == "posted"),
    "ledger.journalentry": ("ledger.JournalEntry", ["ledger.entry.view"], ["ledger.entry.create"],
                            lambda o: o.state == "posted"),
    "purchasing.purchaseorder": ("purchasing.PurchaseOrder", ["purchasing.order.view"], ["purchasing.order.create"],
                                 lambda o: o.state not in ("draft",)),
    "sales.salesorder": ("sales.SalesOrder", ["sales.order.view"], ["sales.order.create"],
                         lambda o: o.state not in ("draft",)),
    "partners.partner": ("partners.Partner", ["partners.partner.view"], ["partners.partner.manage"], lambda o: False),
    "assets.asset": ("assets.Asset", ["assets.asset.view"], ["assets.asset.manage"], lambda o: o.state != "draft"),
    "banking.bankstatement": ("banking.BankStatement", ["banking.statement.view"], ["banking.statement.manage"],
                              lambda o: o.state == "reconciled"),
    "inventory.landedcost": ("inventory.LandedCost", ["inventory.valuation.view"], ["inventory.landed_cost.manage"],
                             lambda o: o.state == "posted"),
}
SIGNATURES = {"pdf": [b"%PDF"], "png": [b"\x89PNG"], "jpg": [b"\xff\xd8\xff"], "jpeg": [b"\xff\xd8\xff"],
              "xlsx": [b"PK\x03\x04"], "docx": [b"PK\x03\x04"], "xls": [b"\xd0\xcf\x11\xe0"], "csv": None, "txt": None}


class AttachmentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.CharField(source="uploaded_by.username", read_only=True)

    class Meta:
        model = Attachment
        fields = ["id", "object_type", "object_id", "original_name", "content_type", "size", "sha256", "description",
                  "uploaded_by_name", "uploaded_at"]


class AttachmentViewSet(viewsets.GenericViewSet):
    serializer_class = AttachmentSerializer
    permission_classes = [ErpPermission]
    perms = {"*": "authenticated"}  # checked per record type below
    parser_classes = [MultiPartParser]

    def _target(self, object_type, object_id, edit=False):
        if object_type not in TYPES:
            raise ValidationError({"object_type": f"One of {', '.join(TYPES)}"})
        model, view_p, edit_p, final = TYPES[object_type]
        perms = effective_permissions(self.request)
        if not perms & set(edit_p if edit else view_p + edit_p):
            raise PermissionDenied("You cannot access attachments of this record type.")
        obj = django_apps.get_model(model).objects.filter(pk=object_id, company=resolve_company(self.request)).first()
        if not obj:
            raise ValidationError({"object_id": "Record not found."})
        return obj, final(obj)

    def list(self, request):
        t, i = request.query_params.get("object_type"), request.query_params.get("object_id")
        self._target(t, i)
        qs = Attachment.objects.filter(company=resolve_company(request), object_type=t, object_id=i)
        return Response(self.get_serializer(qs, many=True).data)

    def create(self, request):
        t, i = request.data.get("object_type"), request.data.get("object_id")
        self._target(t, i, edit=True)
        f = request.FILES.get("file")
        if not f:
            raise ValidationError({"file": "Choose a file."})
        if f.size > settings.ERP_MAX_UPLOAD_MB * 1024 * 1024:
            raise ValidationError({"file": f"Maximum size is {settings.ERP_MAX_UPLOAD_MB} MB."})
        ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
        if ext not in SIGNATURES:
            raise ValidationError({"file": f"Allowed types: {', '.join(SIGNATURES)}."})
        head = f.read(8)
        f.seek(0)
        sig = SIGNATURES[ext]
        if sig and not any(head.startswith(s) for s in sig):
            raise ValidationError({"file": "File content does not match its extension."})
        if sig is None:
            try:
                f.read().decode("utf-8")
            except UnicodeDecodeError:
                raise ValidationError({"file": "Text files must be UTF-8."})
            f.seek(0)
        h = hashlib.sha256()
        for chunk in f.chunks():
            h.update(chunk)
        f.seek(0)
        a = Attachment.objects.create(company=resolve_company(request), object_type=t, object_id=int(i), file=f,
                                      original_name=f.name[:255], size=f.size, sha256=h.hexdigest(),
                                      content_type=mimetypes.guess_type(f.name)[0] or "application/octet-stream",
                                      description=request.data.get("description", "")[:255],
                                      uploaded_by=request.user)
        audit.record("attach", a, {"object": f"{t}:{i}", "name": a.original_name, "sha256": a.sha256},
                     object_type=t, object_id=str(i))
        return Response(self.get_serializer(a).data, status=201)

    def _get(self, pk):
        a = Attachment.objects.filter(pk=pk, company=resolve_company(self.request)).first()
        if not a:
            raise ValidationError({"id": "Not found."})
        return a

    @action(detail=True)
    def download(self, request, pk=None):
        a = self._get(pk)
        self._target(a.object_type, a.object_id)
        resp = FileResponse(a.file.open("rb"), as_attachment=True, filename=a.original_name,
                            content_type=a.content_type)
        resp["X-Content-Type-Options"] = "nosniff"
        return resp

    def destroy(self, request, pk=None):
        a = self._get(pk)
        _, final = self._target(a.object_type, a.object_id, edit=True)
        if final:
            raise BusinessError("Attachments of posted/approved records are evidence and cannot be deleted.",
                                "evidence")
        audit.record("detach", a, {"name": a.original_name, "sha256": a.sha256})
        a.file.delete(save=False)
        a.delete()
        return Response(status=204)
