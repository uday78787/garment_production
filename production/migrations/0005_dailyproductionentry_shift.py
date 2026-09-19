from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("master_data", "0008_shift_minutes_line_shift_manpower"),
        ("production", "0004_dailyproductionentry_defect_pieces"),
    ]

    operations = [
        migrations.AddField(
            model_name="dailyproductionentry",
            name="shift",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="daily_production_entries",
                to="master_data.factoryshift",
            ),
        ),
    ]