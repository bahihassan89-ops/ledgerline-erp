"""Webhook delivery worker. Run continuously:  python manage.py deliver_webhooks --loop

Each request carries:
  X-ERP-Event, X-ERP-Delivery (unique id, use it to de-duplicate),
  X-ERP-Timestamp, X-ERP-Signature: sha256=HMAC_SHA256(secret, f"{timestamp}.{body}")
"""
import hashlib
import hmac
import json
import time
import urllib.request
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.integrations.models import WebhookDelivery
from apps.integrations.serializers import assert_public_url

MAX_ATTEMPTS = 8


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None  # never follow redirects (SSRF)


def send(d):
    body = json.dumps(d.payload, separators=(",", ":"), sort_keys=True).encode()
    ts = str(int(time.time()))
    sig = hmac.new(d.webhook.secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    assert_public_url(d.webhook.url)  # re-check at send time (DNS may have changed)
    req = urllib.request.Request(d.webhook.url, data=body, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": "Ledgerline-ERP-Webhooks/1.0",
        "X-ERP-Event": d.event, "X-ERP-Delivery": d.event_id, "X-ERP-Timestamp": ts,
        "X-ERP-Signature": f"sha256={sig}"})
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(req, timeout=10) as resp:
        return resp.status


class Command(BaseCommand):
    help = "Deliver pending webhooks"

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")

    def handle(self, *args, **opts):
        while True:
            n = self.run_once()
            if not opts["loop"]:
                self.stdout.write(f"processed {n}")
                return
            time.sleep(2 if n else 5)

    def run_once(self):
        with transaction.atomic():
            batch = list(WebhookDelivery.objects.select_for_update(skip_locked=True).select_related("webhook")
                         .filter(status="pending", next_attempt_at__lte=timezone.now())[:50])
            for d in batch:
                d.attempts += 1
                try:
                    code = send(d)
                    d.last_status_code = code
                    d.status = "sent" if 200 <= code < 300 else "pending"
                    d.last_error = ""
                except Exception as e:  # network errors, HTTP errors, SSRF refusal
                    d.last_error = str(e)[:500]
                    d.last_status_code = getattr(e, "code", None)
                    d.status = "pending"
                if d.status == "pending":
                    if d.attempts >= MAX_ATTEMPTS:
                        d.status = "failed"
                    else:
                        d.next_attempt_at = timezone.now() + timedelta(seconds=30 * 2 ** d.attempts)
                d.save()
        return len(batch)
