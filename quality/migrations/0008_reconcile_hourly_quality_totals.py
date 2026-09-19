from django.db import migrations


def reconcile_hourly_totals(apps, schema_editor):
    check_model = apps.get_model("quality", "HourlyLineCheck")
    detail_model = apps.get_model("quality", "HourlyLineCheckDetail")
    for check in check_model.objects.all().iterator():
        details = detail_model.objects.filter(hourly_check_id=check.pk)
        recorded_defects = sum(details.values_list("defect_quantity", flat=True))
        if not recorded_defects:
            continue
        repaired = sum(details.values_list("repaired_quantity", flat=True))
        unresolved = max(recorded_defects - repaired, 0)
        stale_repaired = max(check.defective_quantity - unresolved, 0)
        check.passed_quantity += stale_repaired
        check.defective_quantity = unresolved
        check.inspected_quantity = max(
            check.inspected_quantity,
            check.passed_quantity + check.defective_quantity,
        )
        check.save(update_fields=["passed_quantity", "defective_quantity", "inspected_quantity"])


class Migration(migrations.Migration):
    dependencies = [("quality", "0007_backfill_defect_tracking_codes")]
    operations = [migrations.RunPython(reconcile_hourly_totals, migrations.RunPython.noop)]