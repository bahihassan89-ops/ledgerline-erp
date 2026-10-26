"""Make the presence history permanent (DB-level, like the audit log) and seed the
system-wide GlobalRole bundles (CEO / Central Financial Controller / Central Accounting Manager)."""
from django.db import migrations

from apps.iam.permissions_catalog import GLOBAL_DEFAULT_ROLES

SQL = r"""
CREATE TRIGGER erp_presence_event_append_only BEFORE UPDATE OR DELETE ON iam_presenceevent
  FOR EACH ROW EXECUTE FUNCTION erp_append_only();
CREATE TRIGGER erp_presence_event_no_truncate BEFORE TRUNCATE ON iam_presenceevent
  FOR EACH STATEMENT EXECUTE FUNCTION erp_append_only();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS erp_presence_event_no_truncate ON iam_presenceevent;
DROP TRIGGER IF EXISTS erp_presence_event_append_only ON iam_presenceevent;
"""


def seed_global_roles(apps, schema_editor):
    GlobalRole = apps.get_model("iam", "GlobalRole")
    for name, level, permissions in GLOBAL_DEFAULT_ROLES:
        GlobalRole.objects.update_or_create(
            name=name, defaults={"level": level, "permissions": sorted(permissions)})


def unseed_global_roles(apps, schema_editor):
    GlobalRole = apps.get_model("iam", "GlobalRole")
    GlobalRole.objects.filter(name__in=[name for name, _, _ in GLOBAL_DEFAULT_ROLES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("iam", "0002_globalrole_user_global_roles_userpresence_and_more"),
        ("ledger", "0003_integrity_triggers"),
    ]
    operations = [
        migrations.RunSQL(SQL, REVERSE_SQL),
        migrations.RunPython(seed_global_roles, unseed_global_roles),
    ]
