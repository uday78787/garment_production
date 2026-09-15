from decimal import Decimal

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
        self.assertEqual(dashboard_response.context["qa_today_passed"], 95)
        self.assertEqual(dashboard_response.context["qa_today_defects"], 5)


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
        self.assertContains(response, "Current Shift")
        self.assertContains(response, "Current Hour")
        self.assertNotContains(response, "New Check")
        self.assertNotContains(response, '<label class="form-label">Hour</label>')

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
