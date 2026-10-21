"""First-run setup for a fresh installation: create the first company (Egyptian profile by default)
and its administrator. The admin gets the CFO and System Administrator roles in that company, a
one-time password and must change it at first login. Safe to re-run: existing objects are kept."""
import secrets

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Company
from apps.iam.models import Membership, Role


class Command(BaseCommand):
    help = "Create the first company and its administrator on a fresh installation"

    def add_arguments(self, p):
        p.add_argument("--company-name", required=True)
        p.add_argument("--company-name-ar", default="")
        p.add_argument("--company-code", required=True)
        p.add_argument("--country", default="EG")
        p.add_argument("--admin-email", required=True)
        p.add_argument("--admin-username", default="admin")

    @transaction.atomic
    def handle(self, *a, **o):
        company = Company.objects.filter(code=o["company_code"]).first()
        if company:
            self.stdout.write(f"Company '{company.name}' already exists; keeping it.")
        else:
            call_command("setup_company", name=o["company_name"], name_ar=o["company_name_ar"],
                         code=o["company_code"], country=o["country"], stdout=self.stdout)
            company = Company.objects.get(code=o["company_code"])

        User = get_user_model()
        user = User.objects.filter(username=o["admin_username"]).first()
        password = None
        if user:
            if user.email.lower() != o["admin_email"].lower():
                raise CommandError(f"User '{user.username}' exists with a different email.")
            self.stdout.write(f"User '{user.username}' already exists; password unchanged.")
        else:
            password = secrets.token_urlsafe(12)
            user = User(username=o["admin_username"], email=o["admin_email"], is_staff=False, is_superuser=False,
                        must_change_password=True)
            user.set_password(password)
            user.save()
        m, _ = Membership.objects.get_or_create(user=user, company=company, defaults={"is_default": True})
        m.roles.add(*Role.objects.filter(company=company, name__in=["CFO / Finance Director",
                                                                     "System Administrator"]))
        self.stdout.write(self.style.SUCCESS(f"Ready: company '{company.name}' (code {company.code})."))
        if password:
            self.stdout.write(self.style.SUCCESS(
                f"\n  Admin login:   {user.username}\n  One-time password: {password}\n"
                f"  (At first login you set up an authenticator app and choose a new password.)\n"))
