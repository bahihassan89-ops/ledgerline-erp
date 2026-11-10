from django.db import migrations, models


def backfill_anchor_day(apps, schema_editor):
    """Best-effort recovery for existing templates: next_date.day is the best information we have
    (some may already be drift-clamped from a short month, but that's no worse than before this fix;
    going forward anchor_day stops any further drift)."""
    RecurringEntry = apps.get_model("planning", "RecurringEntry")
    for rec in RecurringEntry.objects.filter(anchor_day__isnull=True):
        rec.anchor_day = rec.next_date.day
        rec.save(update_fields=["anchor_day"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("planning", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="recurringentry",
            name="anchor_day",
            field=models.PositiveSmallIntegerField(null=True, blank=True),
        ),
        migrations.RunPython(backfill_anchor_day, noop),
    ]
