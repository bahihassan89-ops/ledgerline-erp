"""Upgrade an existing company: add any new standard accounts, journals and default settings.
Existing accounts are never modified (except filling in a missing Arabic name)."""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.models import Company

from .setup_company import sync_chart, sync_settings


class Command(BaseCommand):
    help = "Add missing standard accounts/journals/settings to a company"

    def add_arguments(self, p):
        p.add_argument("--code", required=True)

    @transaction.atomic
    def handle(self, *a, code, **o):
        company = Company.objects.get(code=code)
        acc = sync_chart(company)
        sync_settings(company, acc)
        from apps.assets.services import ensure_default_categories
        ensure_default_categories(company, acc)
        self.stdout.write(self.style.SUCCESS(f"{company.name}: chart has {len(acc)} accounts."))
