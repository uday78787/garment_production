from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("quality", "0004_hourlylinecheckdetail_repaired_quantity_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="hourlylinecheckdetail",
            name="tracking_code",
            field=models.CharField(blank=True, editable=False, max_length=8, null=True, unique=True),
        ),
    ]
