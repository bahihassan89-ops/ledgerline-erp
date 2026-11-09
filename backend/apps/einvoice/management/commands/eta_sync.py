"""ETA worker: submit pending e-invoices, refresh statuses, pull received documents.
Run continuously:  python manage.py eta_sync --loop   (or every few minutes from cron)"""
import time

from django.core.management.base import BaseCommand

from apps.einvoice import services
from apps.einvoice.models import EtaSettings


class Command(BaseCommand):
    def add_arguments(self, p):
        p.add_argument("--loop", action="store_true")
        p.add_argument("--received", action="store_true", help="Also pull documents received from vendors")

    def handle(self, *a, loop=False, received=False, **o):
        while True:
            for st in EtaSettings.objects.filter(enabled=True).select_related("company"):
                c = st.company
                try:
                    # auto_submit=False means the company is still testing in preprod ahead of
                    # go-live: submission happens only via the manual submit-pending/resubmit
                    # API actions, not from this background loop. Status refresh and fetching
                    # received documents are harmless either way and always run.
                    s = services.submit_pending(c) if st.auto_submit else 0
                    r = services.refresh_submitted(c)
                    n = services.fetch_received(c) if received else 0
                    if s or r or n:
                        self.stdout.write(f"{c.code}: submitted {s}, refreshed {r}, received {n}")
                except Exception as e:  # keep the worker alive; errors are stored on the documents
                    self.stderr.write(f"{c.code}: {e}")
            if not loop:
                return
            time.sleep(60)
