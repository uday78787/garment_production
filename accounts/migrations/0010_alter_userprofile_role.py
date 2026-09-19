from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0009_alter_userprofile_role")]

    operations = [
        migrations.AlterField(
            model_name="userprofile",
            name="role",
            field=models.CharField(
                choices=[
                    ("SUPERUSER", "Superuser"),
                    ("FACTORY_ADMIN", "Factory Admin"),
                    ("DASHBOARD_VIEWER", "Dashboard Viewer"),
                    ("SUPERVISOR", "Supervisor"),
                    ("QA_PERMISSION", "QA Permission"),
                    ("IE_PERMISSION", "IE Department"),
                ],
                default="SUPERVISOR",
                max_length=20,
            ),
        ),
    ]