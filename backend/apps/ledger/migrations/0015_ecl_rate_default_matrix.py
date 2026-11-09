# Data + schema migration: set sensible default ECL (Expected Credit Loss) provisioning rates.
#
# ecl_rate_current/1_30/31_60/61_90/over_90 were added in 0013, all defaulting to 0, with a
# model comment saying "FC to confirm actual rates". The handbook and the FC review comments
# were checked for a concrete rate matrix and propose none -- only the aging bucket boundaries
# themselves (not due / 1-30 / 31-60 / 61-90 / over 90 days) are specified. Leaving the rates at
# 0% means the "ECL aged receivables" report silently shows no provision for every company until
# someone remembers to configure it by hand, which defeats the point of having the report.
#
# This sets the model field defaults (for brand-new companies going forward) to a standard
# conservative simplified-approach matrix (0% / 5% / 10% / 25% / 50%), and backfills existing
# AccountingSettings rows that still have all five rates at the untouched 0 default -- this field
# was only ever written by this migration's own AddField default, so "all five == 0" reliably
# means "nobody has configured this yet" rather than "the company deliberately wants 0%
# provisioning". Each company's FC/controller should still review and override these via
# Settings based on their own loss history.
from decimal import Decimal

from django.db import migrations, models

CURRENT = Decimal("0")
D_1_30 = Decimal("5")
D_31_60 = Decimal("10")
D_61_90 = Decimal("25")
D_OVER_90 = Decimal("50")


def forwards(apps, schema_editor):
    AccountingSettings = apps.get_model("ledger", "AccountingSettings")
    AccountingSettings.objects.filter(
        ecl_rate_current=0,
        ecl_rate_1_30=0,
        ecl_rate_31_60=0,
        ecl_rate_61_90=0,
        ecl_rate_over_90=0,
    ).update(
        ecl_rate_1_30=D_1_30,
        ecl_rate_31_60=D_31_60,
        ecl_rate_61_90=D_61_90,
        ecl_rate_over_90=D_OVER_90,
    )


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0014_account_ic_counterparty_account_is_intercompany'),
    ]

    operations = [
        migrations.AlterField(
            model_name='accountingsettings',
            name='ecl_rate_1_30',
            field=models.DecimalField(decimal_places=2, default=Decimal('5'), max_digits=5),
        ),
        migrations.AlterField(
            model_name='accountingsettings',
            name='ecl_rate_31_60',
            field=models.DecimalField(decimal_places=2, default=Decimal('10'), max_digits=5),
        ),
        migrations.AlterField(
            model_name='accountingsettings',
            name='ecl_rate_61_90',
            field=models.DecimalField(decimal_places=2, default=Decimal('25'), max_digits=5),
        ),
        migrations.AlterField(
            model_name='accountingsettings',
            name='ecl_rate_over_90',
            field=models.DecimalField(decimal_places=2, default=Decimal('50'), max_digits=5),
        ),
        migrations.RunPython(forwards, backwards),
    ]
