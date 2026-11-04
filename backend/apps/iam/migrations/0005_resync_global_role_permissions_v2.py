"""Re-apply the current GLOBAL_DEFAULT_ROLES bundles onto the already-seeded GlobalRole rows.

The intended business flow is: the CFO or the Accounting Manager drafts a new company and submits
it for approval; the CEO approves it from their Company Approvals queue. "Central Accounting
Manager" was missing company.create/company.submit from GLOBAL_ACCOUNTING_MANAGER in
permissions_catalog.py, so that role could never actually start the flow -- only "Central Financial
Controller" and "CEO" could. This re-seeds the GlobalRole rows the same way 0004 did, so they catch
up to the catalog now that the gap is fixed."""
from django.db import migrations

from apps.iam.permissions_catalog import GLOBAL_DEFAULT_ROLES


def resync_global_roles(apps, schema_editor):
    GlobalRole = apps.get_model("iam", "GlobalRole")
    for name, level, permissions in GLOBAL_DEFAULT_ROLES:
        GlobalRole.objects.update_or_create(
            name=name, defaults={"level": level, "permissions": sorted(permissions)})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("iam", "0004_resync_global_role_permissions"),
    ]
    operations = [
        migrations.RunPython(resync_global_roles, noop),
    ]
