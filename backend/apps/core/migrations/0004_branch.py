# Introduces Branch: every Company gets exactly one `is_main` branch (enforced by the partial
# unique constraint below) created for existing companies by the data migration in
# ledger/0007_journal_branch, which also backfills JournalEntry.branch/JournalLine.branch onto it.
# Branch itself carries no existing data to backfill, so no RunPython is needed here.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_company_name_ar_company_tax_office_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Branch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code", models.CharField(max_length=20)),
                ("name", models.CharField(max_length=120)),
                ("address", models.TextField(blank=True)),
                ("is_main", models.BooleanField(default=False)),
                ("active", models.BooleanField(default=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+",
                                              to="core.company")),
                ("created_by", models.ForeignKey(blank=True, editable=False, null=True,
                                                 on_delete=django.db.models.deletion.PROTECT, related_name="+",
                                                 to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["code"],
            },
        ),
        migrations.AlterUniqueTogether(
            name="branch",
            unique_together={("company", "code")},
        ),
        migrations.AddConstraint(
            model_name="branch",
            constraint=models.UniqueConstraint(condition=models.Q(("is_main", True)), fields=("company",),
                                               name="one_main_branch_per_company"),
        ),
    ]
