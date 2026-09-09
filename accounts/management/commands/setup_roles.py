"""
Management command: python manage.py setup_roles

Idempotently creates the role Groups and assigns simple permissions to each. Safe to
re-run any time (e.g. after new apps/models are added) — it will only add
missing permissions, not remove custom ones an admin has added manually.
"""

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

from master_data.models import Article, Factory, ProductionLine
from planning.models import LineAssignment, ProductionOrder
from production.models import DailyProductionEntry


class Command(BaseCommand):
    help = "Create/update the Admin and Production User groups with default permissions."

    def handle(self, *args, **options):
        models = [Factory, Article, ProductionLine, ProductionOrder, LineAssignment, DailyProductionEntry]
        content_types = [ContentType.objects.get_for_model(model) for model in models]
        all_permissions = Permission.objects.filter(content_type__in=content_types)
        style_permissions = Permission.objects.filter(
            content_type=ContentType.objects.get_for_model(Article),
            codename__in=["add_article", "change_article", "view_article"],
        )
        dashboard_permissions = Permission.objects.filter(
            content_type__in=content_types,
            codename__startswith="view_",
        )
        supervisor_permissions = style_permissions | Permission.objects.filter(
            content_type=ContentType.objects.get_for_model(DailyProductionEntry),
            codename__in=["add_dailyproductionentry", "change_dailyproductionentry", "view_dailyproductionentry"],
        )

        groups = {
            "Superuser": all_permissions,
            "Factory Admin": all_permissions,
            "Supervisor": supervisor_permissions,
            "Dashboard Viewer": dashboard_permissions,
        }
        for name, permissions in groups.items():
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.set(permissions)

        Group.objects.filter(name="Dashboard View").delete()
        Group.objects.filter(name__in=["Admin", "Production User"]).delete()

        self.stdout.write(self.style.SUCCESS("Superuser, Factory Admin, Supervisor, and Dashboard Viewer groups are set up."))
