"""Restrict the application DB role after migrations: the audit log becomes insert-only at the
privilege level (in addition to the append-only trigger)."""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Apply least-privilege grants for the application role (default: erp_app)"

    def add_arguments(self, p):
        p.add_argument("--role", default="erp_app")

    def handle(self, *a, role, **o):
        with connection.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
            if not cur.fetchone():
                self.stdout.write(f"Role {role} does not exist; skipping.")
                return
            q = connection.ops.quote_name(role)
            cur.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {q}")
            cur.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {q}")
            cur.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON core_auditlog FROM {q}")
        self.stdout.write(self.style.SUCCESS(f"Grants applied for {role}."))
