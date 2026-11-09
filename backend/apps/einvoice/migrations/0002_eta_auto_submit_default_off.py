# Data + schema migration: default EtaSettings.auto_submit to OFF (item #11 of the backlog).
#
# auto_submit defaulted to True since the field was introduced, meaning the moment a company
# flips `enabled=True` to start testing ETA in preprod, every posted customer invoice is
# submitted to the Egyptian Tax Authority immediately (and, separately, the eta_sync background
# worker -- apps/einvoice/management/commands/eta_sync.py -- picked up any pending docs within
# ~60 seconds regardless of this flag, a related bug fixed in this same change). There is no
# way to dry-run "build and sign a few test invoices without submitting" with that default.
#
# Checked: as of this migration, every existing EtaSettings row has enabled=False (no company
# has gone live with ETA yet), so flipping auto_submit to False for all of them has no live
# effect on any company currently submitting real documents -- it only changes behaviour for
# whoever enables ETA for the first time going forward, consistent with "default OFF until
# go-live". Idempotent: only touches rows that still have the untouched default (auto_submit=True
# AND enabled=False); a company already live (enabled=True) is left exactly as configured.
from django.db import migrations, models


def forwards(apps, schema_editor):
    EtaSettings = apps.get_model("einvoice", "EtaSettings")
    EtaSettings.objects.filter(enabled=False, auto_submit=True).update(auto_submit=False)


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('einvoice', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='etasettings',
            name='auto_submit',
            field=models.BooleanField(default=False, help_text='Submit automatically when an invoice is posted'),
        ),
        migrations.RunPython(forwards, backwards),
    ]
