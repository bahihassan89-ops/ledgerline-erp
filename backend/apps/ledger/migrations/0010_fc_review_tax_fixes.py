# Data migration for Ibrahim Aly (Financial Controller) review findings, October 2026:
#  1. Mark the four tax control accounts (output/input VAT, WHT payable/receivable) as control
#     accounts, so manual journals can no longer post to them freely.
#  2. Create the "TAX" (Tax Settlements) journal for every existing company, so VAT/WHT settlement
#     and remittance still has a legitimate manual-entry path once (1) is in effect.
#  3. Fix the ETA subtype on the existing WHT 5% commission tax codes (W011 -> W006) and add the
#     two confirmed-missing withholding codes (W003 1% purchases of goods, W010 5% professional
#     fees) for every existing Egyptian company. This mirrors the same fix applied to the
#     setup_company command's EGYPT_TAXES table for newly created companies.
#  4. Refresh the VAT 5% tax names to reflect the Law 149/2026 scope (machinery, equipment and
#     medical devices used in production).
#
# Idempotent: safe to run multiple times (uses get_or_create / targeted updates only).
from django.db import migrations


TAX_CONTROL_CODES = ["110403", "210301", "210304", "110206"]

NEW_EGYPT_TAXES = [
    # (code, name, name_ar, scope, rate, eta_subtype, account_code)
    ("WHT1-GDS-P", "WHT 1% purchases of goods", "خصم وإضافة 1% مشتريات بضائع", "purchase", 1, "W003", "210304"),
    ("WHT5-PRF-P", "WHT 5% professional fees", "خصم وإضافة 5% أتعاب مهنية", "purchase", 5, "W010", "210304"),
    ("WHT1-GDS-S", "WHT 1% purchases of goods (deducted by customer)", "خصم وإضافة 1% مشتريات بضائع (لدى العميل)",
     "sale", 1, "W003", "110206"),
    ("WHT5-PRF-S", "WHT 5% professional fees (deducted by customer)", "خصم وإضافة 5% أتعاب مهنية (لدى العميل)",
     "sale", 5, "W010", "110206"),
]

VAT5_NAMES = {
    "VAT5-S": ("VAT 5% machinery, equipment & medical devices (production use; sales)",
               "ضريبة قيمة مضافة 5% آلات ومعدات وأجهزة طبية (بغرض الإنتاج) - مبيعات"),
    "VAT5-P": ("VAT 5% machinery, equipment & medical devices (production use; purchases)",
               "ضريبة قيمة مضافة 5% آلات ومعدات وأجهزة طبية (بغرض الإنتاج) - مشتريات"),
}


def forwards(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    Journal = apps.get_model("ledger", "Journal")
    Tax = apps.get_model("ledger", "Tax")
    Company = apps.get_model("core", "Company")

    Account.objects.filter(code__in=TAX_CONTROL_CODES).update(is_control=True)

    for company in Company.objects.all():
        Journal.objects.get_or_create(company=company, code="TAX",
                                      defaults={"name": "Tax Settlements", "type": "general", "allow_manual": True})

        if company.country != "EG":
            continue

        Tax.objects.filter(company=company, code__in=["WHT5-COM-P", "WHT5-COM-S"], eta_subtype="W011").update(
            eta_subtype="W006")

        for code, name, name_ar, scope, rate, eta_subtype, acc_code in NEW_EGYPT_TAXES:
            try:
                account = Account.objects.get(company=company, code=acc_code)
            except Account.DoesNotExist:
                continue
            Tax.objects.get_or_create(company=company, code=code, defaults={
                "name": name, "name_ar": name_ar, "scope": scope, "kind": "withholding", "rate": rate,
                "eta_type": "T4", "eta_subtype": eta_subtype, "account": account, "min_base": 300})

        for code, (name, name_ar) in VAT5_NAMES.items():
            Tax.objects.filter(company=company, code=code).update(name=name, name_ar=name_ar)


def backwards(apps, schema_editor):
    # Not reversible in a meaningful way (would re-introduce the uncontrolled-posting gap and the
    # wrong ETA code); intentionally a no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0009_accountingsettings_subcontracting_clearing_account'),
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
