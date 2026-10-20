"""Business events → webhook outbox. Rows are written inside the current transaction, so an
event is only delivered if the business change actually committed."""
import uuid

from django.core.serializers.json import DjangoJSONEncoder
import json


def emit(company, event, payload):
    from .models import Webhook, WebhookDelivery
    hooks = [h for h in Webhook.objects.filter(company=company, active=True) if event in h.events]
    if not hooks:
        return
    body = json.loads(json.dumps(payload, cls=DjangoJSONEncoder))
    WebhookDelivery.objects.bulk_create([
        WebhookDelivery(webhook=h, event=event, event_id=uuid.uuid4().hex,
                        payload={"event": event, "company": company.pk, "data": body})
        for h in hooks
    ])
