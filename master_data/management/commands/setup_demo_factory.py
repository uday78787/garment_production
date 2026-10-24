from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import UserProfile
from master_data.models import (
    Article,
    Factory,
    FactoryShift,
    LineShiftManpower,
    ProductionLine,
    ProductionLineShiftAssignment,
)
from planning.models import LineAssignment, ProductionOrder
from production.models import DailyProductionEntry
from quality.models import (
    DefectMaster,
    HourlyLineCheck,
    HourlyLineCheckDetail,
    InspectionDefect,
    QualityInspection,
)


class Command(BaseCommand):
    help = (
        "Clear database data, preserve Superuser accounts, and create a complete "
        "demo factory. Development only."
    )

    def handle(self, *args, **options):
        if args:
            raise CommandError("Demo setup does not accept positional arguments.")
        verbosity = options.get("verbosity", 1)

        from django.conf import settings

        if not settings.DEBUG:
            raise CommandError(
                "Demo setup clears all database data and is disabled unless DEBUG=True."
            )

        user_model = get_user_model()
        superusers = list(
            user_model.objects.filter(is_superuser=True).values()
        )
        if not superusers:
            raise CommandError(
                "Create a Superuser before running demo setup; no data was changed."
            )

        with transaction.atomic():
            call_command("flush", interactive=False, verbosity=verbosity)
            call_command("setup_roles", verbosity=verbosity)

            for fields in superusers:
                superuser = user_model(**fields)
                superuser.save(force_insert=True)
                profile = UserProfile.objects.get(user=superuser)
                profile.role = UserProfile.Role.SUPERUSER
                profile.save(update_fields=["role"])

            actor = user_model.objects.filter(is_superuser=True).order_by("pk").first()
            if actor is None:
                raise CommandError("Could not restore a Superuser; demo setup was cancelled.")
            self._create_demo_factory(actor)

        if verbosity:
            self.stdout.write(
                self.style.SUCCESS(
                    "Database reset complete. Superusers were preserved and the demo "
                    "factory ecosystem was created."
                )
            )

    def _create_demo_factory(self, actor):
        today = timezone.localdate()
        factory = Factory.objects.create(
            name="Demo Garment Factory",
            code="DEMO01",
            contact_person="Demo Manager",
            phone="0000000000",
            email="demo@example.com",
            city="Tiruppur",
            state="Tamil Nadu",
            country="India",
        )

        shifts = FactoryShift.default_factory_presets(factory, assigned_by=actor)
        day_shift = next(
            shift for shift in shifts
            if shift.preset_code == FactoryShift.PresetShift.DAY
        )

        lines = [
            ProductionLine.objects.create(
                factory=factory,
                line_name=f"DEMO-LINE-{number:02}",
                capacity_manpower=30,
                default_working_minutes=480,
            )
            for number in (1, 2)
        ]
        for line in lines:
            ProductionLineShiftAssignment.objects.create(
                factory=factory,
                production_line=line,
                shift=day_shift,
                assigned_by=actor,
            )
            LineShiftManpower.objects.create(
                factory=factory,
                production_line=line,
                shift=day_shift,
                entry_date=today,
                manpower=24,
                entered_by=actor,
            )

        article = Article.objects.create(
            article_code="DEMO-TSHIRT-001",
            article_name="Demo Cotton T-Shirt",
            sam=Decimal("12.50"),
        )
        order = ProductionOrder.objects.create(
            order_number="DEMO-PO-001",
            article=article,
            order_quantity=5000,
            customer_name="Demo Buyer",
            delivery_date=today + timedelta(days=30),
            status=ProductionOrder.Status.IN_PROGRESS,
        )
        DefectMaster.seed_default_defects()
        broken_stitch = DefectMaster.objects.get(defect_code="DCL-001")

        for line_index, line in enumerate(lines):
            assignment_start = today - timedelta(days=2)
            assignment = LineAssignment.objects.create(
                production_line=line,
                production_order=order,
                start_date=assignment_start,
                default_manpower=24,
                working_minutes_per_operator=480,
                start_efficiency=Decimal("0.40"),
                daily_efficiency_growth=Decimal("0.05"),
                max_efficiency=Decimal("0.60"),
                status=LineAssignment.Status.ACTIVE,
            )
            for day_offset, (efficiency, output) in enumerate(
                (
                    (Decimal("0.45"), 180 + (line_index * 10)),
                    (Decimal("0.50"), 210 + (line_index * 10)),
                    (Decimal("0.55"), 240 + (line_index * 10)),
                )
            ):
                DailyProductionEntry.objects.create(
                    assignment=assignment,
                    shift=day_shift,
                    production_date=assignment_start + timedelta(days=day_offset),
                    actual_manpower=24,
                    working_minutes=480,
                    actual_output=output,
                    defect_pieces=2,
                    planned_efficiency=efficiency,
                    remarks="Sample production data for the demo factory.",
                    created_by=actor,
                    updated_by=actor,
                )

            quality_check = HourlyLineCheck.objects.create(
                factory=factory,
                production_line=line,
                production_order=order,
                article=article,
                check_date=today,
                check_hour=4,
                shift_name=day_shift.shift_name,
                inspected_quantity=60,
                passed_quantity=58,
                defective_quantity=2,
                checker=actor,
                created_by=actor,
                remarks="Sample hourly quality check.",
            )
            HourlyLineCheckDetail.objects.create(
                hourly_check=quality_check,
                defect=broken_stitch,
                defect_quantity=2,
                repaired_quantity=1,
                remarks="Sample defect detail.",
                created_by=actor,
            )
            inspection = QualityInspection.objects.create(
                inspection_number=f"DEMO-QI-{line_index + 1:03}",
                inspection_date=today,
                factory=factory,
                production_line=line,
                production_order=order,
                article=article,
                inspection_type=QualityInspection.InspectionType.END_LINE,
                shift=day_shift.shift_name,
                inspector=actor,
                created_by=actor,
                sample_size=50,
                inspected_quantity=50,
                passed_quantity=48,
                rejected_quantity=2,
                status=QualityInspection.Status.SUBMITTED,
                remarks="Sample end-line inspection for the demo factory.",
            )
            InspectionDefect.objects.create(
                inspection=inspection,
                defect=broken_stitch,
                defect_quantity=2,
                affected_piece_quantity=2,
            )
