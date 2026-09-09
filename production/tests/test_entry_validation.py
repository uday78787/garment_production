from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from master_data.models import Article, Factory, ProductionLine
from planning.models import LineAssignment, ProductionOrder
from production.forms import DailyProductionEntryForm
from production.models import DailyProductionEntry


class ProductionEntryValidationTests(TestCase):
    def setUp(self):
        factory = Factory.objects.create(
            name="Test Factory",
            code="TF",
            contact_person="Test",
            phone="123",
            email="test@example.com",
        )
        line = ProductionLine.objects.create(
            factory=factory,
            line_name="Line 1",
            capacity_manpower=30,
            default_working_minutes=480,
        )
        article = Article.objects.create(
            article_code="ART-001",
            article_name="Test Article",
            sam=Decimal("20.50"),
        )
        order = ProductionOrder.objects.create(
            order_number="PO-1001",
            article=article,
            order_quantity=100,
            customer_name="Customer A",
            delivery_date="2026-08-30",
        )
        self.assignment = LineAssignment.objects.create(
            production_line=line,
            production_order=order,
            start_date="2026-08-01",
            default_manpower=20,
            working_minutes_per_operator=480,
            start_efficiency=Decimal("0.25"),
            daily_efficiency_growth=Decimal("0.05"),
            max_efficiency=Decimal("0.60"),
        )

    def test_pending_production_reflects_order_qty_minus_actual_output(self):
        user = get_user_model().objects.create_user(username="tester", password="testpass123")
        DailyProductionEntry.objects.create(
            assignment=self.assignment,
            production_date="2026-08-02",
            actual_manpower=20,
            actual_output=40,
            planned_efficiency=Decimal("0.50"),
            remarks="",
            created_by=user,
            updated_by=user,
        )
        self.assertEqual(self.assignment.production_order.pending_production, 60)

    def test_working_minutes_drive_available_minutes_target_and_efficiency(self):
        user = get_user_model().objects.create_user(username="minutes-user", password="testpass123")
        entry = DailyProductionEntry.objects.create(
            assignment=self.assignment,
            production_date="2026-08-02",
            actual_manpower=20,
            working_minutes=60,
            actual_output=20,
            planned_efficiency=Decimal("0.50"),
            remarks="Article changeover segment",
            created_by=user,
            updated_by=user,
        )

        self.assertEqual(entry.available_minutes, Decimal("1200.00"))
        self.assertEqual(entry.target_output, Decimal("29.27"))
        self.assertEqual(entry.actual_efficiency, Decimal("0.3417"))

    def test_form_reports_missing_production_date_without_crashing(self):
        form = DailyProductionEntryForm(
            data={
                "assignment": self.assignment.pk,
                "actual_manpower": 20,
                "working_minutes": 60,
                "actual_output": 20,
                "planned_efficiency": 50,
                "remarks": "",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("production_date", form.errors)

    def test_model_reports_missing_production_date_without_type_error(self):
        user = get_user_model().objects.create_user(username="missing-date-user", password="testpass123")
        entry = DailyProductionEntry(
            assignment=self.assignment,
            production_date=None,
            actual_manpower=20,
            working_minutes=60,
            actual_output=20,
            planned_efficiency=Decimal("0.50"),
            created_by=user,
            updated_by=user,
        )

        with self.assertRaises(ValidationError):
            entry.save()

    def test_form_rejects_output_over_order_quantity_with_clear_message(self):
        form = DailyProductionEntryForm(
            data={
                "assignment": self.assignment.pk,
                "production_date": "2026-08-02",
                "actual_manpower": 12,
                "actual_output": 101,
                "planned_efficiency": 50,
                "remarks": "",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("You cannot produce more than the total order quantity", str(form.errors["actual_output"]))

    def test_completed_order_assignment_is_removed_from_selector(self):
        self.assignment.production_order.order_quantity = 40
        self.assignment.production_order.save()
        DailyProductionEntry.objects.create(
            assignment=self.assignment,
            production_date="2026-08-03",
            actual_manpower=20,
            actual_output=40,
            planned_efficiency=Decimal("0.50"),
            remarks="",
            created_by=get_user_model().objects.create_user(username="tester-2", password="testpass123"),
            updated_by=get_user_model().objects.create_user(username="tester-3", password="testpass123"),
        )

        form = DailyProductionEntryForm()
        self.assertNotIn(self.assignment, form.fields["assignment"].queryset)

    def test_editing_existing_entry_without_increasing_output_is_allowed_if_order_is_over_limit(self):
        user = get_user_model().objects.create_superuser(username="over-limit-user", password="testpass123")
        first_entry = DailyProductionEntry.objects.create(
            assignment=self.assignment,
            production_date="2026-08-02",
            actual_manpower=20,
            actual_output=80,
            planned_efficiency=Decimal("0.50"),
            remarks="",
            created_by=user,
            updated_by=user,
        )

        form = DailyProductionEntryForm(
            instance=first_entry,
            user=user,
            selected_factory_id=self.assignment.production_line.factory_id,
            data={
                "assignment": self.assignment.pk,
                "production_date": "2026-08-02",
                "actual_manpower": 20,
                "actual_output": 80,
                "planned_efficiency": 50,
                "remarks": "",
            },
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_form_rejects_increasing_output_when_order_quantity_is_exhausted(self):
        user = get_user_model().objects.create_superuser(username="new-output-user", password="testpass123")
        first_entry = DailyProductionEntry.objects.create(
            assignment=self.assignment,
            production_date="2026-08-02",
            actual_manpower=20,
            actual_output=70,
            planned_efficiency=Decimal("0.50"),
            remarks="",
            created_by=user,
            updated_by=user,
        )

        form = DailyProductionEntryForm(
            instance=first_entry,
            user=user,
            selected_factory_id=self.assignment.production_line.factory_id,
            data={
                "assignment": self.assignment.pk,
                "production_date": "2026-08-02",
                "actual_manpower": 20,
                "actual_output": 101,
                "planned_efficiency": 50,
                "remarks": "",
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn("You cannot produce more than the total order quantity", str(form.errors["actual_output"]))

    def test_save_recomputes_legacy_derived_fields_before_validation(self):
        user = get_user_model().objects.create_superuser(username="legacy-save-user", password="testpass123")
        entry = DailyProductionEntry.objects.create(
            assignment=self.assignment,
            production_date="2026-08-02",
            actual_manpower=20,
            actual_output=10,
            planned_efficiency=Decimal("0.50"),
            remarks="",
            created_by=user,
            updated_by=user,
        )
        entry.actual_efficiency = Decimal("0.123456")
        entry.target_output = Decimal("1234567890123.456")
        entry.save()

        self.assertEqual(entry.actual_efficiency, Decimal("0.0214"))
        self.assertEqual(entry.target_output, Decimal("234.15"))

    def test_superuser_selected_factory_scopes_assignment_list(self):
        other_factory = Factory.objects.create(
            name="Other Factory",
            code="OF",
            contact_person="Other",
            phone="456",
            email="other@example.com",
        )
        other_line = ProductionLine.objects.create(
            factory=other_factory,
            line_name="Line 2",
            capacity_manpower=35,
            default_working_minutes=480,
        )
        other_article = Article.objects.create(
            article_code="ART-002",
            article_name="Other Article",
            sam=Decimal("18.00"),
        )
        other_order = ProductionOrder.objects.create(
            order_number="PO-2001",
            article=other_article,
            order_quantity=60,
            customer_name="Customer B",
            delivery_date="2026-09-01",
        )
        other_assignment = LineAssignment.objects.create(
            production_line=other_line,
            production_order=other_order,
            start_date="2026-08-01",
            default_manpower=20,
            working_minutes_per_operator=480,
            start_efficiency=Decimal("0.25"),
            daily_efficiency_growth=Decimal("0.05"),
            max_efficiency=Decimal("0.60"),
        )

        user = get_user_model().objects.create_superuser(username="superuser", password="testpass123")
        form = DailyProductionEntryForm(user=user, selected_factory_id=self.assignment.production_line.factory_id)

        self.assertIn(self.assignment, form.fields["assignment"].queryset)
        self.assertNotIn(other_assignment, form.fields["assignment"].queryset)
