from django.db import migrations


def normalize_legacy_roles(apps, schema_editor):
    UserProfile = apps.get_model("accounts", "UserProfile")
    UserProfile.objects.filter(role="ADMIN").update(role="FACTORY_ADMIN")


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_alter_userprofile_role")]

    operations = [migrations.RunPython(normalize_legacy_roles, migrations.RunPython.noop)]