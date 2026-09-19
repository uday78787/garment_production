"""
Management command: python manage.py setup_roles

Idempotently creates the role Groups and assigns simple permissions to each. Safe to
re-run any time (e.g. after new apps/models are added) — it will only add
missing permissions, not remove custom ones an admin has added manually.
"""

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

from master_data.models import Article, Factory, LineShiftManpower, ProductionLine
from planning.models import LineAssignment, ProductionOrder
from production.models import DailyProductionEntry
from quality.models import DefectMaster, HourlyLineCheck, HourlyLineCheckDetail, InspectionDefect, QualityInspection


class Command(BaseCommand):
    help = "Create/update the Admin and Production User groups with default permissions."

    def handle(self, *args, **options):
        models = [Factory, Article, ProductionLine, LineShiftManpower, ProductionOrder, LineAssignment, DailyProductionEntry, DefectMaster, QualityInspection, HourlyLineCheck, HourlyLineCheckDetail, InspectionDefect]
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
        qa_permissions = Permission.objects.filter(
            content_type__in=[ContentType.objects.get_for_model(model) for model in [DailyProductionEntry, QualityInspection, HourlyLineCheck, HourlyLineCheckDetail, InspectionDefect]],
            codename__in=["add_dailyproductionentry", "change_dailyproductionentry", "view_dailyproductionentry", "add_qualityinspection", "change_qualityinspection", "view_qualityinspection", "add_hourlylinecheck", "change_hourlylinecheck", "view_hourlylinecheck", "add_hourlylinecheckdetail", "change_hourlylinecheckdetail", "view_hourlylinecheckdetail", "add_inspectiondefect", "change_inspectiondefect", "view_inspectiondefect"],
        )
        ie_permissions = Permission.objects.filter(
            content_type=ContentType.objects.get_for_model(LineShiftManpower),
            codename__in=["add_lineshiftmanpower", "change_lineshiftmanpower", "view_lineshiftmanpower"],
        )

        groups = {
            "Superuser": all_permissions,
            "Factory Admin": all_permissions,
            "Supervisor": supervisor_permissions,
            "Dashboard Viewer": dashboard_permissions,
            "QA Permission": qa_permissions,
            "IE Department": ie_permissions,
        }
        for name, permissions in groups.items():
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.set(permissions)

        Group.objects.filter(name="Dashboard View").delete()
        Group.objects.filter(name__in=["Admin", "Production User"]).delete()

        self.stdout.write(self.style.SUCCESS("Role groups, including IE Department, are set up."))
