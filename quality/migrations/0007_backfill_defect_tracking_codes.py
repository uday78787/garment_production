import secrets

from django.db import migrations


def backfill_tracking_codes(apps, schema_editor):
    detail_model = apps.get_model("quality", "HourlyLineCheckDetail")
    used = set(detail_model.objects.exclude(tracking_code__isnull=True).values_list("tracking_code", flat=True))
    for detail in detail_model.objects.filter(tracking_code__isnull=True):
        code = None
        while code is None or code in used:
            code = str(secrets.randbelow(900000) + 100000)
        detail.tracking_code = code
        detail.save(update_fields=["tracking_code"])
        used.add(code)


class Migration(migrations.Migration):
    dependencies = [("quality", "0006_hourlylinecheck_shift_and_defect_timestamps")]
    operations = [migrations.RunPython(backfill_tracking_codes, migrations.RunPython.noop)]