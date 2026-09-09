from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from dashboard.views import _daily_detail_rows
from master_data.models import Article, Factory, ProductionLine
from planning.models import LineAssignment, ProductionOrder
from production.models import DailyProductionEntry


class DailyDetailExportTests(TestCase):
    def setUp(self):
        self.factory = Factory.objects.create(name="Factory", code="FAC")
        line = ProductionLine.objects.create(
            factory=self.factory, line_name="Line 1", capacity_manpower=30
        )
        article = Article.objects.create(
            article_code="ART", article_name="Article", sam=Decimal("20")
        )
        order = ProductionOrder.objects.create(
            order_number="PO-1", article=article, order_quantity=1000,
            customer_name="Customer", delivery_date=date(2026, 9, 30),
        )
        assignment = LineAssignment.objects.create(
            production_line=line, production_order=order, start_date=date(2026, 9, 1),
            default_manpower=10, working_minutes_per_operator=480,
            start_efficiency=Decimal("0.25"), daily_efficiency_growth=Decimal("0.05"),
            max_efficiency=Decimal("0.60"),
        )
        self.user = get_user_model().objects.create_superuser(
            username="admin", password="password123"
        )
        self.client.force_login(self.user)
        DailyProductionEntry.objects.create(
            assignment=assignment, production_date=date(2026, 9, 10),
            actual_manpower=10, working_minutes=60, actual_output=100,
            planned_efficiency=Decimal("0.50"), created_by=self.user, updated_by=self.user,
        )
        DailyProductionEntry.objects.create(
            assignment=assignment, production_date=date(2026, 9, 11),
            actual_manpower=20, working_minutes=60, actual_output=200,
            planned_efficiency=Decimal("0.50"), created_by=self.user, updated_by=self.user,
        )

    def test_summary_uses_selected_date_range_and_contains_requested_totals(self):
        request = self.client.get(
            reverse("dashboard:export_daily_detail_excel"),
            {"date_from": "2026-09-11", "date_to": "2026-09-11"},
        ).wsgi_request

        rows, summary = _daily_detail_rows(request)

        self.assertEqual(len(rows), 1)
        self.assertEqual(summary["Total Production"], 200)
        self.assertEqual(summary["Avg. Manpower"], Decimal("20"))
        self.assertEqual(summary["Total Available Minutes"], Decimal("1200.00"))
        self.assertEqual(summary["Total Produced Minutes"], Decimal("4000.00"))
        self.assertNotIn("Average Daily Eff %", summary)
        self.assertIn("Avg. Eff %", summary)

    def test_dashboard_download_links_preserve_selected_filters(self):
        response = self.client.get(
            reverse("dashboard:admin_dashboard"),
            {"date_from": "2026-09-10", "date_to": "2026-09-11", "style": "7"},
        )

        self.assertContains(response, "date_from=2026-09-10&amp;date_to=2026-09-11&amp;style=7")