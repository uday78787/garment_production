from decimal import Decimal
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from master_data.models import Article, Factory, FactoryShift, ProductionLine
from planning.models import LineAssignment, ProductionOrder
from quality.calculations import calculate_defect_rate, calculate_dhu, calculate_fpy
from quality.models import DefectMaster, HourlyLineCheck, HourlyLineCheckDetail, InspectionDefect, QualityInspection


class QualityCalculationsTest(TestCase):
    def test_quality_kpis_calculate_expected_values(self):
        self.assertAlmostEqual(calculate_dhu(65, 1000), 6.5)
        self.assertAlmostEqual(calculate_defect_rate(65, 1000), 6.5)
        self.assertAlmostEqual(calculate_fpy(950, 1000), 95.0)


class QualityInspectionIntegrationTest(TestCase):
    def setUp(self):
        self.factory = Factory.objects.create(name="Factory A", code="FA1")
        self.line = ProductionLine.objects.create(
            factory=self.factory,
            line_name="Line 01",
            capacity_manpower=40,
            default_working_minutes=480,
        )
        self.article = Article.objects.create(article_code="A-100", article_name="T-Shirt", sam=Decimal("12.5"))
        self.order = ProductionOrder.objects.create(
            order_number="PO-1001",
            article=self.article,
            order_quantity=1000,
            customer_name="Buyer A",
            delivery_date="2026-10-01",
        )

    def test_inspection_and_defect_flow(self):
        inspection = QualityInspection.objects.create(
            inspection_number="QI-001",
            inspection_date="2026-09-10",
            factory=self.factory,
            production_line=self.line,
            production_order=self.order,
            article=self.article,
            buyer="Buyer A",
            inspected_quantity=1000,
            passed_quantity=940,
            rejected_quantity=20,
            rework_quantity=40,
        )
        defect = DefectMaster.objects.create(
            defect_code="D-01",
            defect_name="Broken Stitch",
            category=DefectMaster.Category.SEWING,
            severity=DefectMaster.Severity.MAJOR,
        )
        InspectionDefect.objects.create(
            inspection=inspection,
            defect=defect,
            defect_quantity=60,
            affected_piece_quantity=45,
        )

        self.assertEqual(inspection.total_defects, 60)
        self.assertAlmostEqual(inspection.dhu, 6.0)
        self.assertAlmostEqual(inspection.fpy, 94.0)


class QualityDefectMasterPageTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="qualityuser", password="pass1234")
        self.user.is_staff = True
        self.user.save()

    def test_defect_master_page_renders(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("quality:defect_master_list"))
        self.assertEqual(response.status_code, 200)


class HourlyQualityCheckTest(TestCase):
    def setUp(self):
        self.factory = Factory.objects.create(name="Factory A", code="FA1")
        self.line = ProductionLine.objects.create(
            factory=self.factory,
            line_name="Line 05",
            capacity_manpower=40,
            default_working_minutes=480,
        )
        self.article = Article.objects.create(article_code="A-200", article_name="Formal Shirt", sam=Decimal("12.5"))
        self.order = ProductionOrder.objects.create(
            order_number="PO-2001",
            article=self.article,
            order_quantity=500,
            customer_name="Buyer B",
            delivery_date="2026-10-01",
        )
        self.user = get_user_model().objects.create_user(username="checker", password="pass1234")

    def test_hourly_end_of_line_check_records_defects_and_dhu(self):
        defect = DefectMaster.objects.create(
            defect_code="D-10",
            defect_name="Open Seam",
            category=DefectMaster.Category.SEWING,
            severity=DefectMaster.Severity.MAJOR,
        )
        check = HourlyLineCheck.objects.create(
            factory=self.factory,
            production_line=self.line,
            production_order=self.order,
            article=self.article,
            check_date="2026-09-10",
            check_hour=9,
            inspected_quantity=240,
            passed_quantity=215,
            defective_quantity=25,
            checker=self.user,
            created_by=self.user,
        )
        check.details.create(defect=defect, defect_quantity=25, remarks="Open seam at sleeve")

        self.assertEqual(check.total_defects, 25)
        self.assertAlmostEqual(check.dhu, 10.416666666666666)

    def test_hourly_check_post_saves_and_redirects(self):
        LineAssignment.objects.create(
            production_line=self.line,
            production_order=self.order,
            start_date="2026-09-01",
            default_manpower=30,
            working_minutes_per_operator=480,
            start_efficiency="0.25",
            daily_efficiency_growth="0.05",
            max_efficiency="0.60",
        )
        self.user.profile.role = UserProfile.Role.QA_PERMISSION
        self.user.profile.factory = self.factory
        self.user.profile.assigned_lines.add(self.line)
        self.user.profile.save()
        self.client.force_login(self.user)
        defect = DefectMaster.objects.create(
            defect_code="D-POST",
            defect_name="Loose Thread",
            category=DefectMaster.Category.SEWING,
            severity=DefectMaster.Severity.MINOR,
        )

        response = self.client.post(
            reverse("quality:hourly_check_create"),
            {
                "production_line": self.line.pk,
                "production_order": self.order.pk,
                "article": self.article.pk,
                "inspected_quantity": "100",
                "passed_quantity": "95",
                "defect_code": defect.defect_code,
                "defect_quantity": "5",
                "repaired_quantity": "5",
                "resolved": "on",
            },
        )

        self.assertRedirects(response, reverse("quality:hourly_check_list"))
        self.assertTrue(HourlyLineCheck.objects.filter(production_line=self.line, inspected_quantity=100).exists())
        dashboard_response = self.client.get(reverse("dashboard:qa_dashboard"))
        self.assertEqual(dashboard_response.context["qa_shift_passed"], 100)
        self.assertEqual(dashboard_response.context["qa_shift_defects"], 0)
        self.assertEqual(dashboard_response.context["qa_current_hour_inspected"], 100)
        self.assertEqual(dashboard_response.context["qa_current_hour_passed"], 100)

    def test_checker_can_mark_repair_and_pass_defect_from_dashboard(self):
        LineAssignment.objects.create(
            production_line=self.line,
            production_order=self.order,
            start_date="2026-09-01",
            default_manpower=30,
            working_minutes_per_operator=480,
            start_efficiency="0.25",
            daily_efficiency_growth="0.05",
            max_efficiency="0.60",
        )
        self.user.profile.role = UserProfile.Role.QA_PERMISSION
        self.user.profile.factory = self.factory
        self.user.profile.assigned_lines.add(self.line)
        self.user.profile.save()
        self.client.force_login(self.user)
        defect = DefectMaster.objects.create(defect_code="D-REPAIR", defect_name="Repairable Seam")

        mark_response = self.client.post(
            reverse("dashboard:qa_mark_defect"),
            data=json.dumps({"line_id": self.line.pk, "defect_code": defect.defect_code, "quantity": 3}),
            content_type="application/json",
        )

        self.assertEqual(mark_response.status_code, 200)
        first_tracking_code = mark_response.json()["tracking_code"]
        self.assertRegex(first_tracking_code, r"^\d{6}$")
        detail = HourlyLineCheckDetail.objects.get(defect=defect)
        self.assertEqual(detail.defect_quantity, 3)
        self.assertEqual(detail.tracking_code, first_tracking_code)
        self.assertIsNotNone(detail.marked_at)
        second_mark_response = self.client.post(
            reverse("dashboard:qa_mark_defect"),
            data=json.dumps({"line_id": self.line.pk, "defect_code": defect.defect_code, "quantity": 1}),
            content_type="application/json",
        )
        self.assertEqual(second_mark_response.status_code, 200)
        self.assertNotEqual(first_tracking_code, second_mark_response.json()["tracking_code"])
        dashboard_response = self.client.get(reverse("dashboard:qa_dashboard"))
        self.assertEqual(len(dashboard_response.context["repair_queue"]), 2)
        self.assertEqual(dashboard_response.context["repair_queue"][0]["qty"], 3)

        repair_response = self.client.post(
            reverse("dashboard:qa_repair_defect"),
            {"detail_id": detail.pk},
        )

        self.assertEqual(repair_response.status_code, 200)
        detail.refresh_from_db()
        detail.hourly_check.refresh_from_db()
        self.assertTrue(detail.resolved)
        self.assertEqual(detail.repaired_quantity, 3)
        self.assertIsNotNone(detail.resolved_at)
        self.assertEqual(detail.hourly_check.passed_quantity, 3)
        self.assertEqual(detail.hourly_check.inspected_quantity, 4)
        self.assertEqual(detail.hourly_check.defective_quantity, 1)
        repair_payload = repair_response.json()
        self.assertEqual(repair_payload["shift_totals"]["inspected"], 4)
        self.assertEqual(repair_payload["shift_totals"]["passed"], 3)
        self.assertEqual(repair_payload["shift_totals"]["defects"], 1)

    def test_checker_pass_actions_are_saved_to_hourly_check(self):
        LineAssignment.objects.create(
            production_line=self.line,
            production_order=self.order,
            start_date="2026-09-01",
            default_manpower=30,
            working_minutes_per_operator=480,
            start_efficiency="0.25",
            daily_efficiency_growth="0.05",
            max_efficiency="0.60",
        )
        self.user.profile.role = UserProfile.Role.QA_PERMISSION
        self.user.profile.factory = self.factory
        self.user.profile.assigned_lines.add(self.line)
        self.user.profile.save()
        self.client.force_login(self.user)

        for _ in range(2):
            response = self.client.post(
                reverse("dashboard:qa_mark_pass"),
                {"line_id": self.line.pk},
            )
            self.assertEqual(response.status_code, 200)

        check = HourlyLineCheck.objects.get(production_line=self.line)
        self.assertEqual(check.inspected_quantity, 2)
        self.assertEqual(check.passed_quantity, 2)
        dashboard_response = self.client.get(reverse("dashboard:qa_dashboard"))
        self.assertEqual(dashboard_response.context["qa_current_hour_inspected"], 2)
        self.assertEqual(dashboard_response.context["qa_current_hour_passed"], 2)

    def test_admin_dashboard_reflects_qa_entry_totals_and_defects(self):
        admin_user = get_user_model().objects.create_superuser(username="adminqa", password="pass1234")
        self.client.force_login(admin_user)
        defect = DefectMaster.objects.create(
            defect_code="D-ADMIN",
            defect_name="Broken Stitch",
            category=DefectMaster.Category.SEWING,
            severity=DefectMaster.Severity.MAJOR,
        )
        check = HourlyLineCheck.objects.create(
            factory=self.factory,
            production_line=self.line,
            production_order=self.order,
            article=self.article,
            check_date=timezone.localdate(),
            check_hour=9,
            shift_name="Day Shift",
            inspected_quantity=100,
            passed_quantity=82,
            defective_quantity=18,
            checker=admin_user,
            created_by=admin_user,
        )
        check.details.create(defect=defect, defect_quantity=18, created_by=admin_user)

        response = self.client.get(reverse("dashboard:admin_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["kpi_total_output"], 100)
        self.assertEqual(response.context["kpi_total_defects"], 18)
        self.assertEqual(len(response.context["line_output"]), 1)
        self.assertEqual(response.context["line_output"][0]["actual_output"], 100)
        self.assertEqual(len(response.context["daily_detail"]), 1)
        self.assertEqual(response.context["daily_detail"][0]["actual_output"], 100)
        self.assertEqual(len(response.context["forecast_assignments"]), 1)
        self.assertContains(response, "Broken Stitch")
        self.assertContains(response, "Defect Breakdown")

    def test_dashboard_shows_shift_totals_and_each_hour_row(self):
        LineAssignment.objects.create(
            production_line=self.line,
            production_order=self.order,
            start_date="2026-09-01",
            default_manpower=30,
            working_minutes_per_operator=480,
            start_efficiency="0.25",
            daily_efficiency_growth="0.05",
            max_efficiency="0.60",
        )
        self.user.profile.role = UserProfile.Role.QA_PERMISSION
        self.user.profile.factory = self.factory
        self.user.profile.assigned_lines.add(self.line)
        self.user.profile.save()
        first_check = HourlyLineCheck.objects.create(
            factory=self.factory, production_line=self.line, production_order=self.order,
            article=self.article, check_date=timezone.localdate(), check_hour=9,
            shift_name="Day Shift", inspected_quantity=5, passed_quantity=4,
            defective_quantity=1, checker=self.user,
        )
        HourlyLineCheck.objects.create(
            factory=self.factory, production_line=self.line, production_order=self.order,
            article=self.article, check_date=timezone.localdate(), check_hour=10,
            shift_name="Day Shift", inspected_quantity=3, passed_quantity=2,
            defective_quantity=1, checker=self.user,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard:qa_dashboard"))
        self.assertEqual(response.context["qa_shift_inspected"], 8)
        self.assertEqual(response.context["qa_shift_passed"], 6)
        self.assertEqual(len(response.context["hourly_report"]), 2)

        api_response = self.client.get(reverse("dashboard:qa_dashboard_data"))
        self.assertEqual(api_response.status_code, 200)
        payload = api_response.json()
        self.assertEqual(payload["shift_totals"]["inspected"], 8)
        self.assertEqual(payload["shift_totals"]["passed"], 6)
        self.assertEqual(len(payload["hourly_report"]), 2)
        self.assertIn("pending_repairs", payload)


class QAPermissionTest(TestCase):
    def test_qa_permission_group_exists_and_uses_line_scope(self):
        user = get_user_model().objects.create_user(username="qauser", password="pass1234")
        user.profile.role = UserProfile.Role.QA_PERMISSION
        user.profile.assigned_lines.add(ProductionLine.objects.create(
            factory=Factory.objects.create(name="Factory B", code="FB1"),
            line_name="Line 10",
            capacity_manpower=35,
            default_working_minutes=480,
        ))
        user.profile.save()

        self.assertTrue(Group.objects.filter(name="QA Permission").exists())
        self.assertIn("QA Permission", [group.name for group in user.groups.all()])
        self.assertTrue(user.profile.assigned_lines.exists())
        self.assertIn("defect_image", [field.name for field in HourlyLineCheckDetail._meta.get_fields()])

    def test_qa_permission_redirects_to_qa_dashboard(self):
        user = get_user_model().objects.create_user(username="qa_redirect", password="pass1234")
        user.profile.role = UserProfile.Role.QA_PERMISSION
        user.profile.save()

        response = self.client.post(
            reverse("accounts:login"),
            {"username": "qa_redirect", "password": "pass1234"},
            follow=True,
        )

        self.assertRedirects(response, reverse("dashboard:qa_dashboard"), fetch_redirect_response=False)

    def test_qa_dashboard_renders_for_qa_user(self):
        factory = Factory.objects.create(name="Factory QA", code="FQA")
        line = ProductionLine.objects.create(
            factory=factory,
            line_name="QA Line 01",
            capacity_manpower=30,
            default_working_minutes=480,
        )
        user = get_user_model().objects.create_user(username="qa_render", password="pass1234")
        user.profile.role = UserProfile.Role.QA_PERMISSION
        user.profile.factory = factory
        user.profile.save()
        user.profile.assigned_lines.add(line)

        self.client.login(username="qa_render", password="pass1234")
        response = self.client.get(reverse("dashboard:qa_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Inspection")
        self.assertContains(response, "Line: QA Line 01")
        self.assertContains(response, "Line Hour Inspection Report")
        self.assertContains(response, "Total Pass pcs")
        self.assertContains(response, "Total Inspected pcs")
        self.assertContains(response, "Total Defect pcs")
        self.assertContains(response, "Resolve Pending Defect")
        self.assertContains(response, "Repair")
        self.assertContains(response, 'data-status="REJECT" disabled')
        self.assertNotContains(response, "New Check")
        self.assertNotContains(response, '<label class="form-label">Hour</label>')

    def test_qa_dashboard_lists_defect_options_for_alter_workflow(self):
        factory = Factory.objects.create(name="Factory Defect", code="FD1")
        line = ProductionLine.objects.create(
            factory=factory,
            line_name="QA Line 02",
            capacity_manpower=30,
            default_working_minutes=480,
        )
        DefectMaster.objects.create(defect_code="D-100", defect_name="Broken Stitch", active=True)
        user = get_user_model().objects.create_user(username="qa_defect_check", password="pass1234")
        user.profile.role = UserProfile.Role.QA_PERMISSION
        user.profile.factory = factory
        user.profile.save()
        user.profile.assigned_lines.add(line)

        self.client.login(username="qa_defect_check", password="pass1234")
        response = self.client.get(reverse("dashboard:qa_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mark defect")
        self.assertContains(response, "Broken Stitch")

    def test_factory_shift_supports_custom_hours_and_overtime(self):
        factory = Factory.objects.create(name="Shift Factory", code="SF1")
        shift = FactoryShift.objects.create(
            factory=factory,
            shift_name="Main Shift",
            start_time="08:00:00",
            end_time="20:00:00",
            standard_hours=8,
            overtime_hours=2,
            overtime_start_time="16:00:00",
            active=True,
        )

        self.assertEqual(shift.standard_hours, 8)
        self.assertEqual(shift.overtime_hours, 2)
        self.assertEqual(shift.total_shift_hours, 10)
        self.assertEqual(shift.shift_end_time().strftime("%H:%M"), "18:00")

    def test_superuser_can_manage_factory_shift_schedule_from_dashboard(self):
        factory = Factory.objects.create(name="Factory Admin UI", code="FAUI")
        superuser = get_user_model().objects.create_superuser(username="superui", password="pass1234")

        self.client.force_login(superuser)
        response = self.client.get(reverse("dashboard:shift_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Factory Shift Management")

        create_response = self.client.post(
            reverse("dashboard:shift_management"),
            {
                "factory": str(factory.pk),
                "shift_name": "Day Shift",
                "start_time": "08:00",
                "end_time": "16:00",
                "standard_hours": "8",
                "overtime_hours": "0",
                "overtime_start_time": "",
                "description": "Factory day schedule",
                "active": "on",
            },
        )

        self.assertEqual(create_response.status_code, 302)
        self.assertTrue(FactoryShift.objects.filter(factory=factory, shift_name="Day Shift").exists())
