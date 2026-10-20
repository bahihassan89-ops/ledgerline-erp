from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.iam.access import ErpPermission, resolve_company


class CompanyViewSet(viewsets.ModelViewSet):
    """Base viewset: everything is filtered to the caller's company, and the company and
    creator are set server-side (clients can never choose them)."""
    permission_classes = [ErpPermission]
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    @property
    def company(self):
        return resolve_company(self.request)

    def get_queryset(self):
        return super().get_queryset().filter(company=self.company)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if getattr(self, "request", None) and self.request.user.is_authenticated:
            ctx["company"] = self.company
        return ctx

    def perform_create(self, serializer):
        serializer.save(company=self.company, created_by=self.request.user)

    def create(self, request, *args, **kwargs):
        """Supports `Idempotency-Key`: a retried POST with the same key returns the original
        response instead of creating a duplicate document."""
        key = request.headers.get("Idempotency-Key")
        if not key:
            return self.create_document(request, *args, **kwargs)
        from apps.integrations.models import IdempotencyRecord
        if len(key) > 100:
            raise ValidationError({"Idempotency-Key": "Max 100 characters."})
        prev = IdempotencyRecord.objects.filter(company=self.company, user=request.user, key=key).first()
        if prev:
            if prev.path != request.path:
                raise ValidationError({"Idempotency-Key": "Key already used for a different endpoint."})
            return Response(prev.body, status=prev.status_code, headers={"Idempotent-Replay": "true"})
        response = self.create_document(request, *args, **kwargs)
        IdempotencyRecord.objects.create(company=self.company, user=request.user, key=key, path=request.path,
                                         status_code=response.status_code, body=response.data)
        return response

    def create_document(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)


class CompanyReadOnlyViewSet(CompanyViewSet):
    http_method_names = ["get", "head", "options"]
