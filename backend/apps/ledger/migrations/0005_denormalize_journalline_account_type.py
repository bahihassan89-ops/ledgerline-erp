# account_type is denormalized from ledger_account onto ledger_journalline so the dashboard
# and reports can filter/group by account type via an index instead of a join (see the
# comment on JournalLine.account_type in models.py -- under load testing with ~1M lines, the
# join-based filter fell back to a full parallel sequential scan of the line table).
#
# This backfills every existing line from its account before the supporting index is built,
# matching this project's established AddField(default="")+RunSQL-backfill pattern for adding
# a non-nullable column to an already-populated table.

from django.db import migrations, models


def backfill_account_type(apps, schema_editor):
    schema_editor.execute(
        "UPDATE ledger_journalline l SET account_type = a.account_type "
        "FROM ledger_account a WHERE a.id = l.account_id AND l.account_type = ''"
    )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_company_name_ar_company_tax_office_and_more'),
        ('inventory', '0004_stockmove_manufacturing_order_and_more'),
        ('ledger', '0004_account_cash_flow_category_account_name_ar_and_more'),
        ('partners', '0002_partner_building_number_partner_eta_receiver_type_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='journalline',
            name='account_type',
            field=models.CharField(blank=True, choices=[('Group', 'Group'), ('Cash', 'Cash'), ('Bank', 'Bank'), ('Receivable', 'Receivable'), ('Current Asset', 'Current Asset'), ('Inventory', 'Inventory'), ('Prepayment', 'Prepayment'), ('Tax Input', 'Tax Input'), ('Wht Receivable', 'Wht Receivable'), ('Notes Receivable', 'Notes Receivable'), ('Wip', 'Wip'), ('Fixed Asset', 'Fixed Asset'), ('Accumulated Depreciation', 'Accumulated Depreciation'), ('Non Current Asset', 'Non Current Asset'), ('Payable', 'Payable'), ('Current Liability', 'Current Liability'), ('Tax Output', 'Tax Output'), ('Grni', 'Grni'), ('Wht Payable', 'Wht Payable'), ('Notes Payable', 'Notes Payable'), ('Non Current Liability', 'Non Current Liability'), ('Equity', 'Equity'), ('Retained Earnings', 'Retained Earnings'), ('Revenue', 'Revenue'), ('Other Income', 'Other Income'), ('Cost Of Sales', 'Cost Of Sales'), ('Expense', 'Expense'), ('Depreciation', 'Depreciation'), ('Other Expense', 'Other Expense')], max_length=30),
        ),
        migrations.RunPython(backfill_account_type, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name='journalline',
            index=models.Index(fields=['company', 'account_type', 'posted', 'date'], name='ledger_jour_company_771914_idx'),
        ),
    ]
