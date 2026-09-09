from django.db import migrations, models


def copy_primary_factory_access(apps, schema_editor):
    UserProfile = apps.get_model("accounts", "UserProfile")
    for profile in UserProfile.objects.exclude(factory__isnull=True):
        profile.accessible_factories.add(profile.factory_id)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0006_userprofile_one_factory_admin_per_factory")]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="accessible_factories",
            field=models.ManyToManyField(
                blank=True,
                help_text="Factories this user is allowed to access. Superusers can access all factories.",
                related_name="authorized_users",
                to="master_data.factory",
            ),
        ),
        migrations.RunPython(copy_primary_factory_access, migrations.RunPython.noop),
    ]