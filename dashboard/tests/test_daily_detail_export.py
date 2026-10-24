from datetime import date, datetime, timedelta
from decimal import Decimal
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from dashboard.views import _daily_detail_rows, AdminDashboardView
from master_data.models import Article, Factory, FactoryShift, LineShiftManpower, ProductionLine
from planning.models import LineAssignment, ProductionOrder
from production.models import DailyProductionEntry
from quality.models import HourlyLineCheck, HourlyLineSummary


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

    def test_admin_and_user_dashboards_offer_manual_refresh_and_background_sync(self):
        for dashboard_name in ("dashboard:admin_dashboard", "dashboard:user_dashboard"):
            with self.subTest(dashboard=dashboard_name):
                response = self.client.get(reverse(dashboard_name))
                self.assertContains(response, 'id="dashboard-refresh-button"')
                self.assertContains(response, "Dashboard data syncs every 10 seconds")
                self.assertContains(response, "dashboardSyncIntervalMs = 10000")
                self.assertContains(response, 'id="dashboard-live-content"')
                self.assertContains(response, 'id="dashboard-live-scripts"')
                self.assertNotContains(response, "Auto-refreshing in")

    def test_admin_dashboard_defaults_to_current_day_for_live_data(self):
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        line = self.factory.production_lines.first()
        article = Article.objects.create(article_code="ART2", article_name="Article 2", sam=Decimal("18"))
        order = ProductionOrder.objects.create(
            order_number="PO-2",
            article=article,
            order_quantity=800,
            customer_name="Customer 2",
            delivery_date=today + timedelta(days=5),
        )
        assignment = LineAssignment.objects.create(
            production_line=line,
            production_order=order,
            start_date=yesterday,
            default_manpower=10,
            working_minutes_per_operator=480,
            start_efficiency=Decimal("0.25"),
            daily_efficiency_growth=Decimal("0.05"),
            max_efficiency=Decimal("0.60"),
        )
        DailyProductionEntry.objects.create(
            assignment=assignment,
            production_date=yesterday,
            actual_manpower=10,
            working_minutes=60,
            actual_output=50,
            planned_efficiency=Decimal("0.50"),
            created_by=self.user,
            updated_by=self.user,
        )
        DailyProductionEntry.objects.create(
            assignment=assignment,
            production_date=today,
            actual_manpower=12,
            working_minutes=60,
            actual_output=120,
            planned_efficiency=Decimal("0.50"),
            created_by=self.user,
            updated_by=self.user,
        )

        response = self.client.get(reverse("dashboard:admin_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(row["date"] == today for row in response.context["daily_detail"]))
        self.assertEqual(float(response.context["kpi_total_output"]), 120.0)

    def test_admin_daily_output_chart_has_a_separate_series_for_each_line(self):
        today = timezone.localdate()
        first_assignment = LineAssignment.objects.select_related(
            "production_line", "production_order"
        ).first()
        second_line = ProductionLine.objects.create(
            factory=self.factory,
            line_name="Line 2",
            capacity_manpower=30,
        )
        second_article = Article.objects.create(
            article_code="ART-L2",
            article_name="Line 2 Article",
            sam=Decimal("22"),
        )
        second_order = ProductionOrder.objects.create(
            order_number="PO-L2",
            article=second_article,
            order_quantity=1000,
            customer_name="Customer",
            delivery_date=today + timedelta(days=30),
        )
        second_assignment = LineAssignment.objects.create(
            production_line=second_line,
            production_order=second_order,
            start_date=today,
            default_manpower=10,
            working_minutes_per_operator=480,
            start_efficiency=Decimal("0.25"),
            daily_efficiency_growth=Decimal("0.05"),
            max_efficiency=Decimal("0.60"),
        )
        for assignment, output in ((first_assignment, 125), (second_assignment, 75)):
            DailyProductionEntry.objects.create(
                assignment=assignment,
                production_date=today,
                actual_manpower=10,
                working_minutes=60,
                actual_output=output,
                planned_efficiency=Decimal("0.50"),
                created_by=self.user,
                updated_by=self.user,
            )

        response = self.client.get(reverse("dashboard:admin_dashboard"))

        self.assertEqual(response.status_code, 200)
        datasets = json.loads(response.context["chart_line_datasets"])
        chart_labels = json.loads(response.context["chart_labels"])
        self.assertEqual([dataset["label"] for dataset in datasets], ["Line 1", "Line 2"])
        today_index = chart_labels.index(today.strftime("%d-%b"))
        self.assertEqual([dataset["data"][today_index] for dataset in datasets], [125.0, 75.0])
        self.assertEqual(sum(dataset["data"][today_index] for dataset in datasets), response.context["kpi_total_output"])
        efficiency_series = json.loads(response.context["chart_efficiency_series"])
        self.assertEqual(efficiency_series[today_index], response.context["kpi_avg_efficiency"])
        self.assertContains(response, "type: 'bar'")
        self.assertContains(response, "label: 'Average Efficiency'")
        self.assertContains(response, "type: 'line'")
        self.assertContains(response, "pointRadius: 4")
        self.assertContains(response, "Efficiency (%)")
        self.assertContains(response, "context.parsed.y}%")

    def test_admin_dashboard_current_hour_uses_hour_number_label(self):
        factory = Factory.objects.create(name="Factory 2", code="FAC2")
        ProductionLine.objects.create(factory=factory, line_name="Line 2", capacity_manpower=30)
        shift = FactoryShift.objects.create(
            factory=factory,
            shift_name="Day Shift",
            start_time="08:00:00",
            end_time="16:00:00",
            standard_hours=8,
            active=True,
            hourly_schedule=[
                {"hour": 1, "start": "08:00", "end": "09:00", "break_start": "", "break_end": ""},
                {"hour": 2, "start": "09:00", "end": "10:00", "break_start": "", "break_end": ""},
            ],
        )
        self.user = get_user_model().objects.create_superuser(username="admin2", password="password123")
        self.client.force_login(self.user)
        request = self.client.get(reverse("dashboard:admin_dashboard")).wsgi_request
        request.session["selected_factory_id"] = factory.pk
        view = AdminDashboardView()
        view.request = request
        context = view.get_context_data()

        self.assertTrue(context["current_hour_label"].startswith("Hour "))
        self.assertEqual(context["current_hour_label"], shift.current_schedule_status(current_dt=timezone.now())["label"]) 

    def test_factory_shift_auto_schedule_supports_lunch_and_overtime(self):
        shift = FactoryShift(
            factory=self.factory,
            shift_name="Day Shift",
            start_time="09:00:00",
            end_time="17:30:00",
            standard_hours=8,
            overtime_hours=3,
            overtime_start_time="17:30:00",
        )

        schedule = shift.build_hourly_schedule(lunch_break_start="12:00", lunch_break_end="12:30")

        self.assertEqual(schedule[0]["hour"], 1)
        self.assertEqual(schedule[0]["start"], "09:00")
        self.assertEqual(schedule[0]["end"], "10:00")
        self.assertEqual(schedule[3]["break_start"], "12:00")
        self.assertEqual(schedule[3]["break_end"], "12:30")
        self.assertEqual(schedule[-1]["end"], "20:30")
        self.assertEqual(shift.total_shift_hours, 11)

    def test_factory_shift_rejects_malformed_time_values(self):
        self.assertIsNone(FactoryShift._coerce_time("not-a-time"))
        self.assertIsNone(FactoryShift._coerce_time(object()))

    def test_qa_dashboard_counts_ie_manpower_once_per_line_shift(self):
        line = self.factory.production_lines.first()
        article = Article.objects.get(article_code="ART")
        order = ProductionOrder.objects.get(order_number="PO-1")
        shift = FactoryShift.objects.create(
            factory=self.factory,
            shift_name="Day Shift",
            start_time="09:00:00",
            end_time="17:30:00",
            standard_hours=8,
            shift_minutes=480,
            active=True,
        )
        LineShiftManpower.objects.create(
            factory=self.factory,
            production_line=line,
            shift=shift,
            entry_date=timezone.localdate(),
            manpower=25,
            entered_by=self.user,
        )
        for check_hour in (9, 10):
            HourlyLineCheck.objects.create(
                factory=self.factory,
                production_line=line,
                production_order=order,
                article=article,
                check_date=timezone.localdate(),
                check_hour=check_hour,
                shift_name=shift.shift_name,
                inspected_quantity=10,
                passed_quantity=10,
                created_by=self.user,
            )

        _, detail_rows, _ = AdminDashboardView().get_qa_reporting_data(
            selected_factory_id=self.factory.pk,
            date_from=timezone.localdate(),
            date_to=timezone.localdate(),
        )

        self.assertEqual(len(detail_rows), 1)
        self.assertEqual(detail_rows[0]["manpower"], 25)
        self.assertEqual(detail_rows[0]["available_minutes"], Decimal("12000"))
        self.assertEqual(detail_rows[0]["produced_minutes"], Decimal("400"))
        self.assertEqual(detail_rows[0]["actual_efficiency"], Decimal("3.333333333333333333333333333"))
        self.assertEqual(detail_rows[0]["planned_efficiency"], Decimal("60.0"))
        self.assertEqual(detail_rows[0]["target_output"], Decimal("360"))
        self.assertEqual(detail_rows[0]["defect_pieces"], 0)
        self.assertEqual(detail_rows[0]["dhu"], Decimal("0"))
        self.assertEqual(detail_rows[0]["difference"], Decimal("-340"))

    def test_hourly_summary_uses_matching_shift_manpower_not_latest_row(self):
        line = self.factory.production_lines.first()
        article = Article.objects.get(article_code="ART")
        order = ProductionOrder.objects.get(order_number="PO-1")
        day = timezone.localdate()
        day_shift = FactoryShift.objects.create(
            factory=self.factory,
            shift_name="Day Shift",
            start_time="09:00:00",
            end_time="17:30:00",
            standard_hours=8,
            shift_minutes=480,
            active=True,
        )
        ot_shift = FactoryShift.objects.create(
            factory=self.factory,
            shift_name="Over Time",
            start_time="17:30:00",
            end_time="20:30:00",
            standard_hours=3,
            shift_minutes=180,
            active=True,
        )
        LineShiftManpower.objects.create(
            factory=self.factory,
            production_line=line,
            shift=ot_shift,
            entry_date=day,
            manpower=10,
            entered_by=self.user,
        )
        LineShiftManpower.objects.create(
            factory=self.factory,
            production_line=line,
            shift=day_shift,
            entry_date=day,
            manpower=25,
            entered_by=self.user,
        )

        check = HourlyLineCheck.objects.create(
            factory=self.factory,
            production_line=line,
            production_order=order,
            article=article,
            check_date=day,
            check_hour=9,
            shift_name="Day Shift",
            inspected_quantity=80,
            passed_quantity=75,
            created_by=self.user,
        )

        summary = check.sync_hourly_summary()

        self.assertEqual(summary.manpower, 25)
        self.assertEqual(summary.working_minutes, 480)
        self.assertEqual(summary.shift_name, "Day Shift")

    def test_line_summary_lines_are_scoped_to_selected_factory(self):
        other_factory = Factory.objects.create(name="Factory 2", code="FAC2")
        other_line = ProductionLine.objects.create(factory=other_factory, line_name="Other Line", capacity_manpower=20)
        current_line = self.factory.production_lines.first()

        self.client.force_login(self.user)
        session = self.client.session
        session["selected_factory_id"] = self.factory.pk
        session.save()

        response = self.client.get(reverse("dashboard:admin_dashboard"))

        self.assertEqual(response.status_code, 200)
        visible_lines = [line.line_name for line in response.context["line_summary_lines"]]
        self.assertIn(current_line.line_name, visible_lines)
        self.assertNotIn(other_line.line_name, visible_lines)

    def test_line_summary_uses_live_hourly_admin_records_not_stale_summary_rows(self):
        line = self.factory.production_lines.first()
        article = Article.objects.get(article_code="ART")
        order = ProductionOrder.objects.get(order_number="PO-1")
        shift = FactoryShift.objects.create(
            factory=self.factory,
            shift_name="Day Shift",
            start_time="09:00:00",
            end_time="17:30:00",
            standard_hours=8,
            shift_minutes=480,
            active=True,
        )
        LineShiftManpower.objects.create(
            factory=self.factory,
            production_line=line,
            shift=shift,
            entry_date=date(2026, 9, 10),
            manpower=25,
            entered_by=self.user,
        )
        check = HourlyLineCheck.objects.create(
            factory=self.factory,
            production_line=line,
            production_order=order,
            article=article,
            check_date=date(2026, 9, 10),
            check_hour=9,
            shift_name="Day Shift",
            inspected_quantity=80,
            passed_quantity=75,
            created_by=self.user,
        )
        HourlyLineSummary.objects.create(
            factory=self.factory,
            production_line=line,
            production_order=order,
            article=article,
            summary_date=date(2026, 9, 10),
            shift_name="Day Shift",
            check_hour=10,
            inspected_quantity=999,
            passed_quantity=999,
            defect_quantity=999,
            manpower=99,
            working_minutes=999,
            available_minutes=Decimal("9999"),
            produced_minutes=Decimal("9999"),
            actual_output=999,
            target_output=Decimal("999"),
            hourly_efficiency=Decimal("99"),
            dhu=Decimal("99"),
        )

        response = self.client.get(
            reverse("dashboard:admin_dashboard"),
            {"line_summary_line": line.pk, "line_summary_date": date(2026, 9, 10).isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        actual_values = [row["actual_output"] for row in response.context["line_summary_report"]]
        self.assertIn(check.inspected_quantity, actual_values)
        self.assertNotIn(999, actual_values)
        self.assertTrue(any(row["production_line_id"] == line.pk for row in response.context["line_summary_report"]))

    def test_line_summary_does_not_double_count_same_shift_manpower_across_hours(self):
        line = self.factory.production_lines.first()
        article = Article.objects.get(article_code="ART")
        order = ProductionOrder.objects.get(order_number="PO-1")
        shift = FactoryShift.objects.create(
            factory=self.factory,
            shift_name="Day Shift",
            start_time="09:00:00",
            end_time="17:30:00",
            standard_hours=8,
            shift_minutes=480,
            active=True,
        )
        LineShiftManpower.objects.create(
            factory=self.factory,
            production_line=line,
            shift=shift,
            entry_date=date(2026, 9, 10),
            manpower=25,
            entered_by=self.user,
        )
        for hour in (9, 10):
            HourlyLineCheck.objects.create(
                factory=self.factory,
                production_line=line,
                production_order=order,
                article=article,
                check_date=date(2026, 9, 10),
                check_hour=hour,
                shift_name="Day Shift",
                inspected_quantity=40,
                passed_quantity=38,
                created_by=self.user,
            )

        response = self.client.get(
            reverse("dashboard:admin_dashboard"),
            {"line_summary_line": line.pk, "line_summary_date": date(2026, 9, 10).isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["line_summary_report"]), 1)
        totals = response.context["line_summary_report"][0]
        self.assertEqual(totals["manpower"], 25)
        self.assertEqual(totals["available_minutes"], Decimal("12000"))
        self.assertEqual(totals["produced_minutes"], Decimal("1600"))

    def test_dashboard_keeps_all_dates_in_range_and_prefers_qa_when_present(self):
        line = self.factory.production_lines.first()
        article = Article.objects.get(article_code="ART")
        order = ProductionOrder.objects.get(order_number="PO-1")
        day_one = date(2026, 9, 10)
        day_two = date(2026, 9, 11)
        FactoryShift.objects.create(
            factory=self.factory,
            shift_name="Day Shift",
            start_time="09:00:00",
            end_time="17:30:00",
            standard_hours=8,
            shift_minutes=480,
            active=True,
        )
        HourlyLineCheck.objects.create(
            factory=self.factory,
            production_line=line,
            production_order=order,
            article=article,
            check_date=day_two,
            check_hour=9,
            shift_name="Day Shift",
            inspected_quantity=20,
            passed_quantity=18,
            created_by=self.user,
        )

        response = self.client.get(
            reverse("dashboard:admin_dashboard"),
            {"date_from": day_one.isoformat(), "date_to": day_two.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        dates = [row["date"] for row in response.context["daily_detail"]]
        self.assertEqual(dates, [day_one, day_two])
        self.assertEqual(response.context["quality_source"], "QA")
        self.assertEqual(response.context["qa_daily_summary"][0]["date"], day_two)
        self.assertEqual(response.context["qa_daily_summary"][0]["shift"], "Day Shift")