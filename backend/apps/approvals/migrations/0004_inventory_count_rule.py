# Data migration for Ibrahim Aly (Financial Controller) review findings, October 2026: large
# inventory count adjustments (write-offs/write-ons from physical counts) had no approval gate --
# anyone who could post a count could silently book any size of variance straight to the GL. This
# adds a default ApprovalRule for every existing company, mirroring the rule setup_company.py now
# creates for brand-new companies: variances of EGP 25,000 (or local-currency equivalent) or more
# need Controller (level 4) sign-off before the StockMove is booked -- see
# inventory.services.apply_count / apps.inventory.models.InventoryCount.
#
# Idempotent: skips any company that already has an inventory_count rule (e.g. re-run, or a company
# created after this migration shipped, which already gets the rule from setup_company.py).
from decimal import Decimal

from django.db import migrations


def forwards(apps, schema_editor):
    Company = apps.get_model("core", "Company")
    ApprovalRule = apps.get_model("approvals", "ApprovalRule")

    for company in Company.objects.all():
        if ApprovalRule.objects.filter(company=company, document_type="inventory_count").exists():
            continue
        k = Decimal(10) if company.base_currency_id == "EGP" else Decimal(1)
        ApprovalRule.objects.create(
            company=company, name="Large inventory count variance → Controller",
            document_type="inventory_count", min_amount=Decimal(25000) * k, approver_levels=[4])


def backwards(apps, schema_editor):
    # Not reversible in a meaningful way (would re-open the no-approval-gate gap); no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('approvals', '0003_doc_exists_trigger'),
        ('core', '0001_initial'),
        ('inventory', '0007_inventorycount'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
