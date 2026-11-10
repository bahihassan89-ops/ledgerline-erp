"""Backfill the new department roles (Inventory Officer/Manager, Procurement Officer/Manager)
onto every already-existing company, and additively grant the new iam.team.manage permission
(plus iam.user.manage/iam.role.manage, so the Controller/CFO who usually created the company --
see apps.core.services._grant_submitter_membership -- can actually staff it, not just the
System Administrator) onto existing role rows of that name.

Additive only: never removes or overwrites a permission a company admin may have already added to
or removed from one of these rows by hand, and never touches a role a company renamed away from
these defaults. New companies get all of this for free from DEFAULT_ROLES in setup_company.setup();
this migration only catches up companies that existed before this feature did."""
from decimal import Decimal

from django.db import migrations

from apps.iam.permissions_catalog import DEFAULT_ROLES

ADDITIVE_GRANTS = {
    "System Administrator": {"iam.team.manage"},
    "Financial Controller": {"iam.user.manage", "iam.role.manage", "iam.team.manage"},
    "CFO / Finance Director": {"iam.user.manage", "iam.role.manage", "iam.team.manage"},
}

NEW_ROLE_NAMES = {"Inventory Officer", "Inventory Manager", "Procurement Officer", "Procurement Manager"}


def forwards(apps, schema_editor):
    Company = apps.get_model("core", "Company")
    Role = apps.get_model("iam", "Role")
    by_name = {name: (level, perms, limit) for name, level, perms, limit in DEFAULT_ROLES}
    for company in Company.objects.all():
        k = Decimal(10) if company.base_currency_id == "EGP" else Decimal(1)
        for role in Role.objects.filter(company=company, name__in=ADDITIVE_GRANTS):
            missing = ADDITIVE_GRANTS[role.name] - set(role.permissions)
            if missing:
                role.permissions = sorted(set(role.permissions) | missing)
                role.save(update_fields=["permissions"])
        existing_names = set(Role.objects.filter(company=company).values_list("name", flat=True))
        for name in NEW_ROLE_NAMES - existing_names:
            level, perms, limit = by_name[name]
            Role.objects.create(company=company, name=name, level=level, permissions=sorted(set(perms)),
                                approval_limit=Decimal(limit) * k if limit is not None else None)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("iam", "0006_team_model"),
    ]

    operations = [
        migrations.RunPython(forwards, noop),
    ]
