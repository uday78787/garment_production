from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("production", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="dailyproductionentry",
            name="working_minutes",
            field=models.PositiveIntegerField(
                default=480,
                help_text="Actual minutes worked by each operator on this article.",
                validators=[MinValueValidator(0)],
            ),
        ),
    ]