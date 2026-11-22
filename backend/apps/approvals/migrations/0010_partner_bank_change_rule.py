# Item #34 (Ibrahim Aly / Financial Controller review, October 2026, §6/§17(7)): "Vendor master: a
# change of bank details needs maker-checker plus an independent call-back to a known number."
# Unlike inventory_count (0004) or the returns rules, there is no "small enough to skip" case here
# -- any bank-detail change is a BEC/vendor-impersonation fraud vector, so the rule always matches
# at min_amount=0. Mirrors the per-company backfill pattern from 0004_inventory_count_rule.py; new
# companies get the same rule going forward from setup_company.py.
#
# Idempotent: skips any company that already has a partner_bank_change rule.
from django.db import migrations


def forwards(apps, schema_editor):
    Company = apps.get_model("core", "Company")
    ApprovalRule = apps.get_model("approvals", "ApprovalRule")

    for company in Company.objects.all():
        if ApprovalRule.objects.filter(company=company, document_type="partner_bank_change").exists():
            continue
        ApprovalRule.objects.create(
            company=company, name="Vendor/customer bank-detail change → Controller",
            document_type="partner_bank_change", min_amount=0, approver_levels=[4])


def backwards(apps, schema_editor):
    # Not reversible in a meaningful way (would re-open the no-approval-gate gap); no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('approvals', '0009_doc_exists_trigger_partner_bank_change'),
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
