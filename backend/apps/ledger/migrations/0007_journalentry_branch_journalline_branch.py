# Every JournalEntry now belongs to exactly one Branch (see core.Branch and the docstring on
# JournalEntry.branch in models.py); JournalLine.branch is a denormalized copy of it, same
# rationale as account_type (0005/0006): branch-level reports filter/group over the whole line
# table, and a join to ledger_journalentry has no supporting index at this table's scale.
#
# Backfill strategy for pre-existing data: every Company that doesn't already have a Branch gets
# one synthetic main branch ("MAIN" / "Main Branch"); every existing JournalEntry/JournalLine is
# then assigned to its company's main branch. This is the only sane default for data that
# predates the branch concept -- it's exactly what setup_company now does going forward for new
# companies, so old and new companies end up in the same shape (one main branch, more addable).
#
# JournalEntry.branch is added nullable first so the backfill can run, then tightened to
# NOT NULL in the same migration -- the established AddField+RunPython-backfill+AlterField
# pattern in this project (see 0005 for the nullable-column variant of the same idea).

import django.db.models.deletion
from django.db import migrations, models


def backfill_branches(apps, schema_editor):
    Company = apps.get_model("core", "Company")
    Branch = apps.get_model("core", "Branch")
    JournalEntry = apps.get_model("ledger", "JournalEntry")
    JournalLine = apps.get_model("ledger", "JournalLine")

    for company in Company.objects.all():
        main = Branch.objects.filter(company=company, is_main=True).first()
        if not main:
            main = Branch.objects.create(company=company, code="MAIN", name="Main Branch", is_main=True)
        JournalEntry.objects.filter(company=company, branch__isnull=True).update(branch=main)
        JournalLine.objects.filter(company=company, branch__isnull=True).update(branch=main)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_branch"),
        ("ledger", "0006_trigger_check_account_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="journalentry",
            name="branch",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="entries", to="core.branch",
                                    help_text="Defaults to the company's main branch when not specified."),
        ),
        migrations.AddField(
            model_name="journalline",
            name="branch",
            field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="+", to="core.branch"),
        ),
        migrations.RunPython(backfill_branches, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="journalentry",
            name="branch",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="entries",
                                    to="core.branch",
                                    help_text="Defaults to the company's main branch when not specified."),
        ),
        migrations.AddIndex(
            model_name="journalline",
            index=models.Index(fields=["company", "branch", "posted", "date"],
                               name="ledger_jour_branch_idx"),
        ),
    ]
