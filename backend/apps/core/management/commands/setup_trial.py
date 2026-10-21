"""Trial installation: load the Egyptian demo company and give every demo user a random password
(the shared demo password is published in the README, so it must never be live on a public server)."""
import secrets

from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.iam.models import User

from .seed_demo import DEMO_PASSWORD, USERS


class Command(BaseCommand):
    help = "Load the Egyptian demo company with one user per role, each with a random password"

    def handle(self, *a, **o):
        call_command("seed_demo_eg", stdout=self.stdout)
        rows = []
        for uname, role, *_ in USERS:
            u = User.objects.filter(username=uname).first()
            if not u:
                continue
            if not u.password or u.check_password(DEMO_PASSWORD):
                pw = secrets.token_urlsafe(10)
                u.set_password(pw)
                u.save()
            else:
                pw = "(unchanged)"
            rows.append((uname, role, pw))
        self.stdout.write(self.style.SUCCESS("\n  Trial users (company: Nile Industries SAE)\n"))
        for uname, role, pw in rows:
            self.stdout.write(f"  {uname:<11} {role:<24} {pw}")
        self.stdout.write("\n  Controller and CFO set up an authenticator app at first login.\n")
