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
        p.add_argument("--update-roles", action="store_true",
                       help="Add newly introduced default permissions to the standard roles (never removes any)")

    @transaction.atomic
    def handle(self, *a, code, update_roles=False, **o):
        company = Company.objects.get(code=code)
        acc = sync_chart(company)
        sync_settings(company, acc)
        from apps.assets.services import ensure_default_categories
        ensure_default_categories(company, acc)
        if update_roles:
            from apps.iam.models import Role
            from apps.iam.permissions_catalog import DEFAULT_ROLES
            for name, level, perms, limit in DEFAULT_ROLES:
                role = Role.objects.filter(company=company, name=name).first()
                if role:
                    role.permissions = sorted(set(role.permissions) | set(perms))
                    role.save(update_fields=["permissions"])
        self.stdout.write(self.style.SUCCESS(f"{company.name}: chart has {len(acc)} accounts."))
