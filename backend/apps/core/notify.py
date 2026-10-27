"""Cross-cutting notification helpers -- create an in-app Notification and, if the recipient
has registered a browser/phone for push, attempt delivery there too (see push.py). Mirrors the
audit.record() pattern: other apps import this instead of touching Notification directly."""
import logging

from .models import Notification

logger = logging.getLogger(__name__)


def send(user, title, body="", url="", category=Notification.Category.SYSTEM, company_id=None):
    if user is None:
        return None
    notif = Notification.objects.create(
        user=user, company_id=company_id, category=category, title=title[:200], body=body[:500], url=url[:255],
    )
    _push(notif)
    return notif


def send_many(users, title, body="", url="", category=Notification.Category.SYSTEM, company_id=None):
    return [send(u, title, body, url, category, company_id) for u in {u.pk: u for u in users}.values()]


def _push(notif):
    from .push import send_push
    try:
        send_push(notif)
    except Exception:
        logger.exception("push delivery failed for notification %s", notif.pk)
