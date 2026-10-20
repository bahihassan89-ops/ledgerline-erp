"""Daily job: python manage.py run_recurring"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import Company
from apps.planning.services import run_due


class Command(BaseCommand):
    def handle(self, *a, **o):
        for c in Company.objects.filter(active=True):
            n = len(run_due(c, timezone.localdate()))
            if n:
                self.stdout.write(f"{c.code}: {n} recurring entries created")
