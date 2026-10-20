from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api import CompanyReadOnlyViewSet
from apps.iam.access import ErpPermission, resolve_company

from . import services
from .models import EtaDocument, EtaReceivedDocument, EtaSettings


class EtaSettingsSerializer(serializers.ModelSerializer):
    client_secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    signer_token = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_secret = serializers.SerializerMethodField()

    class Meta:
        model = EtaSettings
        exclude = ["id", "company"]

    def get_has_secret(self, obj):
        return bool(obj.client_secret)

    def validate(self, attrs):
        env = attrs.get("environment", getattr(self.instance, "environment", "preprod"))
        mode = attrs.get("signer_mode", getattr(self.instance, "signer_mode", "service"))
        if env == "prod" and mode == "unsigned_preprod":
            raise serializers.ValidationError({"signer_mode": "Production requires a signing agent (USB token)."})
        url = attrs.get("signer_url")
        if url and not url.startswith("https://") and not url.startswith("http://127.0.0.1") \
                and not url.startswith("http://localhost"):
            raise serializers.ValidationError({"signer_url": "Use HTTPS (or a local agent on localhost)."})
        for f in ("client_secret", "signer_token"):  # empty = keep the stored secret
            if f in attrs and not attrs[f]:
                attrs.pop(f)
        return attrs


class EtaSettingsView(APIView):
    permission_classes = [ErpPermission]
    perms = {"get": "einvoice.document.view", "patch": "einvoice.settings.manage"}

    def get(self, request):
        st, _ = EtaSettings.objects.get_or_create(company=resolve_company(request))
        return Response(EtaSettingsSerializer(st).data)

    def patch(self, request):
        st, _ = EtaSettings.objects.get_or_create(company=resolve_company(request))
        s = EtaSettingsSerializer(st, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data)


class EtaDocumentSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    partner = serializers.CharField(source="invoice.partner.name", read_only=True)
    date = serializers.DateField(source="invoice.date", read_only=True)
    total = serializers.DecimalField(source="invoice.amount_total", max_digits=20, decimal_places=4, read_only=True)
    url = serializers.SerializerMethodField()

    class Meta:
        model = EtaDocument
        fields = ["id", "invoice", "invoice_number", "partner", "date", "total", "document_type", "internal_id",
                  "state", "uuid", "long_id", "submission_uuid", "errors", "attempts", "submitted_at", "validated_at",
                  "url"]

    def get_url(self, obj):
        return obj.public_url()


class EtaDocumentViewSet(CompanyReadOnlyViewSet):
    queryset = EtaDocument.objects.select_related("invoice", "invoice__partner")
    serializer_class = EtaDocumentSerializer
    http_method_names = ["get", "post", "head", "options"]
    perms = {"list": "einvoice.document.view", "payload": "einvoice.document.view",
             "received": "einvoice.document.view", "cancel": "einvoice.document.cancel",
             "*": "einvoice.document.submit"}
    filterset_fields = ["state", "document_type"]
    search_fields = ["internal_id", "uuid", "invoice__partner__name"]

    @action(detail=False, methods=["post"], url_path="submit-pending")
    def submit_pending(self, request):
        return Response({"submitted": services.submit_pending(self.company),
                         "refreshed": services.refresh_submitted(self.company)})

    @action(detail=True, methods=["post"])
    def refresh(self, request, pk=None):
        return Response(self.get_serializer(services.refresh(self.get_object())).data)

    @action(detail=True, methods=["post"])
    def resubmit(self, request, pk=None):
        doc = services.resubmit(self.get_object())
        services.submit_pending(self.company, docs=[doc])
        doc.refresh_from_db()
        return Response(self.get_serializer(doc).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        doc = services.request_cancel(self.get_object(), request.data.get("reason", ""), request.user)
        return Response(self.get_serializer(doc).data)

    @action(detail=True)
    def payload(self, request, pk=None):
        doc = self.get_object()
        st = services.eta_settings(self.company)
        body = doc.payload or services.build_document(doc.invoice, st)
        return Response({"document": body, "canonical": services.serialize(body)})

    @action(detail=False, methods=["get", "post"])
    def received(self, request):
        if request.method == "POST":
            services.fetch_received(self.company)
        rows = EtaReceivedDocument.objects.filter(company=self.company).select_related("bill")[:500]
        return Response([{"uuid": r.uuid, "internal_id": r.internal_id, "issuer_id": r.issuer_id,
                          "issuer_name": r.issuer_name, "date": r.date_issued, "total": str(r.total),
                          "status": r.status, "bill": r.bill.number if r.bill else None} for r in rows])
