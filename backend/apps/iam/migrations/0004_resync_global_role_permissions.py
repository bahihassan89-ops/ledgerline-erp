"""Re-apply the current GLOBAL_DEFAULT_ROLES bundles onto the already-seeded GlobalRole rows.

Migration 0003 seeded CEO / Central Financial Controller / Central Accounting Manager once, with
whatever GLOBAL_CATALOG looked like at the time. permissions_catalog.py has since grown two
report codes (report.intercompany, report.consolidated, for the Group Reports page) and a backup
code (ops.backup.manage, for the on-demand backup button) -- none of which ever reached the
GlobalRole rows already sitting in the database, since migrations only run once. This re-seeds
them the same way 0003 did, so the roles catch up to the catalog."""
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
        ("iam", "0003_presence_log_and_global_roles"),
    ]
    operations = [
        migrations.RunPython(resync_global_roles, noop),
    ]
