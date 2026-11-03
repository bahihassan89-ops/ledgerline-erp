# Data migration: backfill AccountingSettings.cheque_bounce_fee_account (added in 0011) for every
# existing company, pointing at the existing "Bank Charges" expense account (520306) -- mirrors
# how sync_settings() wires DEFAULT_ACCOUNTS for brand-new companies (apps/core/management/coa.py).
# Part of the FC review's cheque-handling fixes: lets billing.services.cheque_bounce() post a real
# bounce fee instead of requiring every company to configure the account by hand first.
#
# Idempotent: only sets the field where it's still empty, and only where the account code exists.
from django.db import migrations


def forwards(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    AccountingSettings = apps.get_model("ledger", "AccountingSettings")

    for st in AccountingSettings.objects.filter(cheque_bounce_fee_account__isnull=True).select_related("company"):
        account = Account.objects.filter(company=st.company, code="520306").first()
        if account:
            st.cheque_bounce_fee_account = account
            st.save(update_fields=["cheque_bounce_fee_account"])


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0011_accountingsettings_cheque_bounce_fee_account'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
