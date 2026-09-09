from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserProfile
from master_data.models import Article, Factory, ProductionLine
from planning.models import LineAssignment, ProductionOrder
from production.models import DailyProductionEntry


class ReportAccessTests(TestCase):
    def setUp(self):
        self.factory = Factory.objects.create(name="Factory", code="FAC")
        self.other_factory = Factory.objects.create(name="Other", code="OTH")
        article = Article.objects.create(article_code="ART", article_name="Article", sam=Decimal("20"))
        self.line = ProductionLine.objects.create(
            factory=self.factory, line_name="Line 1", capacity_manpower=20
        )
        self.other_line = ProductionLine.objects.create(
            factory=self.factory, line_name="Line 2", capacity_manpower=20
        )
        other_factory_line = ProductionLine.objects.create(
            factory=self.other_factory, line_name="Line 3", capacity_manpower=20
        )
        order_one = ProductionOrder.objects.create(
            order_number="PO-1", article=article, order_quantity=100,
            customer_name="Customer", delivery_date=date(2026, 9, 1),
        )
        order_two = ProductionOrder.objects.create(
            order_number="PO-2", article=article, order_quantity=100,
            customer_name="Customer", delivery_date=date(2026, 9, 1),
        )
        order_three = ProductionOrder.objects.create(
            order_number="PO-3", article=article, order_quantity=100,
            customer_name="Customer", delivery_date=date(2026, 9, 1),
        )
        self.assignment = self._assignment(self.line, order_one)
        self.other_assignment = self._assignment(self.other_line, order_two)
        self.other_factory_assignment = self._assignment(other_factory_line, order_three)
        self.user = get_user_model().objects.create_user(username="supervisor", password="password")
        self.user.profile.role = UserProfile.Role.SUPERVISOR
        self.user.profile.factory = self.factory
        self.user.profile.assigned_lines.add(self.line)
        self.user.profile.save()
        self.client.login(username="supervisor", password="password")
        for assignment in (self.assignment, self.other_assignment, self.other_factory_assignment):
            DailyProductionEntry.objects.create(
                assignment=assignment, production_date=date(2026, 8, 2),
                actual_manpower=10, working_minutes=60, actual_output=1,
                planned_efficiency=Decimal("0.5"),
                created_by=self.user, updated_by=self.user,
            )

    def _assignment(self, line, order):
        return LineAssignment.objects.create(
            production_line=line, production_order=order, start_date=date(2026, 8, 1),
            default_manpower=10, working_minutes_per_operator=480,
            start_efficiency=Decimal("0.25"), daily_efficiency_growth=Decimal("0.05"),
            max_efficiency=Decimal("0.60"),
        )

    def test_report_page_only_shows_assigned_line(self):
        response = self.client.get(reverse("reports:production_report"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PO-1")
        self.assertNotContains(response, "PO-2")
        self.assertNotContains(response, "PO-3")

    def test_report_exports_require_login(self):
        self.client.logout()
        for name in ("reports:export_excel", "reports:export_pdf"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302)

    def test_selected_factory_cannot_expand_line_scope(self):
        session = self.client.session
        session["selected_factory_id"] = self.factory.pk
        session.save()
        response = self.client.get(reverse("reports:production_report"))
        self.assertContains(response, "PO-1")
        self.assertNotContains(response, "PO-2")

    def test_report_date_filter_is_inclusive_and_preserved_for_exports(self):
        DailyProductionEntry.objects.create(
            assignment=self.assignment, production_date=date(2026, 8, 10),
            actual_manpower=10, working_minutes=60, actual_output=2,
            planned_efficiency=Decimal("0.5"), created_by=self.user, updated_by=self.user,
        )

        response = self.client.get(
            reverse("reports:production_report"),
            {"date_from": "2026-08-10", "date_to": "2026-08-10"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["entries"].count(), 1)
        self.assertContains(response, "10 Aug 2026")
        self.assertContains(response, "date_from=2026-08-10&amp;date_to=2026-08-10")
        self.assertNotContains(response, "02 Aug 2026")
