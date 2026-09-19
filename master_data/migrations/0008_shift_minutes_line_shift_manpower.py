from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("master_data", "0007_factoryshift_hourly_schedule")]

    operations = [
        migrations.AddField(
            model_name="factoryshift",
            name="shift_minutes",
            field=models.PositiveSmallIntegerField(
                default=480,
                help_text="Planned working minutes for this shift.",
                validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(1440)],
            ),
        ),
        migrations.CreateModel(
            name="LineShiftManpower",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("entry_date", models.DateField(default=django.utils.timezone.localdate)),
                ("manpower", models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(0)])),
                ("working_minutes", models.PositiveIntegerField(editable=False, validators=[django.core.validators.MinValueValidator(1)])),
                ("entered_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="manpower_entries_entered", to=settings.AUTH_USER_MODEL)),
                ("factory", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="line_shift_manpower", to="master_data.factory")),
                ("production_line", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="shift_manpower", to="master_data.productionline")),
                ("shift", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="manpower_entries", to="master_data.factoryshift")),
            ],
            options={"ordering": ["-entry_date", "production_line__line_name", "shift__start_time"]},
        ),
        migrations.AddConstraint(
            model_name="lineshiftmanpower",
            constraint=models.UniqueConstraint(fields=("production_line", "shift", "entry_date"), name="unique_line_shift_manpower_date"),
        ),
    ]