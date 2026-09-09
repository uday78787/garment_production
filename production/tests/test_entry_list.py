from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from master_data.models import Article, Factory, ProductionLine
from planning.models import LineAssignment, ProductionOrder
from production.models import DailyProductionEntry


class ProductionEntryListTests(TestCase):
    def setUp(self):
        factory = Factory.objects.create(
            name="Test Factory",
            code="TF",
            contact_person="Test",
            phone="123",
            email="test@example.com",
        )
        self.line_one = ProductionLine.objects.create(
            factory=factory,
            line_name="Line 1",
            capacity_manpower=30,
            default_working_minutes=480,
        )
        self.line_two = ProductionLine.objects.create(
            factory=factory,
            line_name="Line 2",
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
            order_quantity=1000,
            customer_name="Customer A",
            delivery_date="2026-08-30",
        )
        self.assignment_one = self._create_assignment(self.line_one, order)
        self.assignment_two = self._create_assignment(self.line_two, order)
        self.user = get_user_model().objects.create_superuser(
            username="admin", password="testpass123", email="admin@example.com"
        )

    @staticmethod
    def _create_assignment(line, order):
        return LineAssignment.objects.create(
            production_line=line,
            production_order=order,
            start_date="2026-08-01",
            default_manpower=20,
            working_minutes_per_operator=480,
            start_efficiency=Decimal("0.25"),
            daily_efficiency_growth=Decimal("0.05"),
            max_efficiency=Decimal("0.60"),
        )

    def _create_entry(self, assignment, production_date, actual_output=40):
        return DailyProductionEntry.objects.create(
            assignment=assignment,
            production_date=production_date,
            actual_manpower=20,
            actual_output=actual_output,
            planned_efficiency=Decimal("0.50"),
            created_by=self.user,
            updated_by=self.user,
        )

    def test_date_filter_returns_only_matching_entries(self):
        matching = self._create_entry(self.assignment_one, "2026-08-02")
        self._create_entry(self.assignment_two, "2026-08-03")
        self.client.force_login(self.user)

        response = self.client.get(reverse("production:entry_list"), {"date": "2026-08-02"})

        self.assertEqual(list(response.context["entries"]), [matching])
        self.assertContains(response, 'value="2026-08-02"')

    def test_line_filter_returns_only_selected_line_and_preserves_selection(self):
        matching = self._create_entry(self.assignment_two, "2026-08-02")
        self._create_entry(self.assignment_one, "2026-08-02")
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("production:entry_list"), {"line": str(self.line_two.pk)}
        )

        self.assertEqual(list(response.context["entries"]), [matching])
        self.assertEqual(response.context["selected_line_id"], str(self.line_two.pk))
        self.assertContains(response, f'<option value="{self.line_two.pk}" selected>')

    def test_month_filter_includes_leap_day_only_for_the_selected_month(self):
        leap_day = self._create_entry(self.assignment_one, "2028-02-29")
        self._create_entry(self.assignment_two, "2028-03-01")
        self.client.force_login(self.user)

        response = self.client.get(reverse("production:entry_list"), {"month": "2028-02"})

        self.assertEqual(list(response.context["entries"]), [leap_day])
        self.assertEqual(response.context["selected_month"], "2028-02")
        self.assertContains(response, 'value="2028-02"')

    def test_entries_are_newest_first_then_stable_by_line(self):
        older = self._create_entry(self.assignment_two, "2026-08-01")
        same_date_line_two = self._create_entry(self.assignment_two, "2026-08-02")
        same_date_line_one = self._create_entry(self.assignment_one, "2026-08-02")
        self.client.force_login(self.user)

        response = self.client.get(reverse("production:entry_list"))

        self.assertEqual(
            list(response.context["entries"]),
            [same_date_line_one, same_date_line_two, older],
        )