from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import CompanyReadOnlyViewSet, CompanyViewSet

from .models import WEBHOOK_EVENTS, ApiKey, Webhook, WebhookDelivery
from .serializers import ApiKeySerializer, WebhookDeliverySerializer, WebhookSerializer


class ApiKeyViewSet(CompanyViewSet):
    queryset = ApiKey.objects.select_related("owner")
    serializer_class = ApiKeySerializer
    perms = {"*": "integrations.apikey.manage"}
    http_method_names = ["get", "post", "patch", "head", "options"]

    def create(self, request, *args, **kwargs):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        # Key acts on behalf of its creator; it can never exceed the creator's rights.
        obj, full_key = ApiKey.generate(company=self.company, owner=request.user, created_by=request.user,
                                        **s.validated_data)
        data = self.get_serializer(obj).data
        data["key"] = full_key
        data["warning"] = "Store this key now. It will not be shown again."
        return Response(data, status=201)


class WebhookViewSet(CompanyViewSet):
    queryset = Webhook.objects.all()
    serializer_class = WebhookSerializer
    perms = {"*": "integrations.webhook.manage"}

    def create(self, request, *args, **kwargs):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        obj = s.save(company=self.company, created_by=request.user)
        data = self.get_serializer(obj).data
        data["secret"] = obj.secret
        data["warning"] = "Use this secret to verify the X-ERP-Signature header. It will not be shown again."
        return Response(data, status=201)

    @action(detail=False)
    def events(self, request):
        return Response(WEBHOOK_EVENTS)


class WebhookDeliveryViewSet(CompanyReadOnlyViewSet):
    queryset = WebhookDelivery.objects.all()
    serializer_class = WebhookDeliverySerializer
    perms = {"*": "integrations.webhook.manage"}
    filterset_fields = ["status", "event", "webhook"]

    def get_queryset(self):
        return WebhookDelivery.objects.filter(webhook__company=self.company)
