from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("production", "0005_dailyproductionentry_shift"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="dailyproductionentry",
            name="unique_assignment_per_day",
        ),
        migrations.AddConstraint(
            model_name="dailyproductionentry",
            constraint=models.UniqueConstraint(
                fields=("assignment", "production_date", "shift"),
                condition=models.Q(shift__isnull=False),
                name="unique_assignment_date_shift",
            ),
        ),
        migrations.AddConstraint(
            model_name="dailyproductionentry",
            constraint=models.UniqueConstraint(
                fields=("assignment", "production_date"),
                condition=models.Q(shift__isnull=True),
                name="unique_assignment_date_no_shift",
            ),
        ),
    ]
