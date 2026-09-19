import random

from django.db import migrations, models
from django.utils import timezone


def assign_six_digit_codes(apps, schema_editor):
    detail_model = apps.get_model("quality", "HourlyLineCheckDetail")
    used_codes = set(
        detail_model.objects.exclude(tracking_code__isnull=True)
        .exclude(tracking_code="")
        .values_list("tracking_code", flat=True)
    )
    generator = random.SystemRandom()
    for detail in detail_model.objects.all().iterator():
        if detail.tracking_code and len(detail.tracking_code) == 6:
            continue
        while True:
            code = str(generator.randrange(100000, 1000000))
            if code not in used_codes:
                used_codes.add(code)
                break
        detail.tracking_code = code
        detail.save(update_fields=["tracking_code"])


def reverse_codes(apps, schema_editor):
    detail_model = apps.get_model("quality", "HourlyLineCheckDetail")
    detail_model.objects.update(tracking_code=None)


class Migration(migrations.Migration):
    dependencies = [
        ("quality", "0005_hourlylinecheckdetail_tracking_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="hourlylinecheck",
            name="shift_name",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AlterField(
            model_name="hourlylinecheckdetail",
            name="tracking_code",
            field=models.CharField(blank=True, editable=False, max_length=6, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="hourlylinecheckdetail",
            name="marked_at",
            field=models.DateTimeField(default=timezone.now, editable=False),
        ),
        migrations.AddField(
            model_name="hourlylinecheckdetail",
            name="resolved_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(assign_six_digit_codes, reverse_codes),
    ]
