from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserProfile
from master_data.models import Article, Factory, ProductionLine
from planning.models import ProductionOrder
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
