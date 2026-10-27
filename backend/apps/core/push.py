"""Web Push delivery (VAPID). Best-effort only: a failed/expired push subscription is pruned,
everything else is logged and swallowed by notify._push() -- the in-app notification already
exists regardless of whether push delivery succeeds."""
import json
import logging

from django.conf import settings

from .models import PushSubscription

logger = logging.getLogger(__name__)


def send_push(notif):
    if not settings.WEBPUSH_PRIVATE_KEY:
        return
    from pywebpush import WebPushException, webpush

    payload = json.dumps({"id": notif.pk, "title": notif.title, "body": notif.body, "url": notif.url})
    for sub in PushSubscription.objects.filter(user_id=notif.user_id):
        try:
            webpush(
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=payload,
                vapid_private_key=settings.WEBPUSH_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{settings.WEBPUSH_CONTACT_EMAIL}"},
            )
        except WebPushException as e:
            status = e.response.status_code if e.response is not None else None
            if status in (404, 410):
                sub.delete()  # endpoint expired or unsubscribed on the client
            else:
                logger.warning("push failed for subscription %s: %s", sub.pk, e)
