from datetime import timedelta
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.models import UserProfile
from master_data.models import Article, Factory, FactoryShift, LineShiftManpower, ProductionLine, Size
from planning.models import LineAssignment, ProductionOrder
from quality.calculations import calculate_defect_rate, calculate_dhu, calculate_fpy
from quality.models import DefectMaster, HourlyLineCheck, HourlyLineCheckDetail, InspectionDefect, QualityInspection
from quality.uploads import sanitize_defect_image


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
        self.size = Size.objects.create(name="Medium")
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
            size=self.size,
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

    def test_inspection_form_saves_selected_master_size(self):
        LineAssignment.objects.create(
            production_line=self.line,
            production_order=self.order,
            start_date="2026-09-01",
            default_manpower=30,
            start_efficiency="0.25",
            daily_efficiency_growth="0.05",
            max_efficiency="0.60",
        )
        user = get_user_model().objects.create_superuser(
            username="inspection-size-admin",
            email="inspection-size-admin@example.com",
            password="test-password",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("quality:inspection_create"),
            {
                "inspection_date": "2026-09-10",
                "factory": self.factory.pk,
                "production_line": self.line.pk,
                "production_order": self.order.pk,
                "article": self.article.pk,
                "size": self.size.pk,
                "inspected_quantity": "10",
                "passed_quantity": "10",
            },
        )

        self.assertEqual(response.status_code, 200)
        inspection = QualityInspection.objects.get(inspection_number__startswith="QI-")
        self.assertEqual(inspection.size, self.size)

    def test_inspection_form_lists_master_sizes(self):
        user = get_user_model().objects.create_superuser(
            username="inspection-size-viewer",
            email="inspection-size-viewer@example.com",
            password="test-password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("quality:inspection_create"))

        self.assertContains(response, f'<option value="{self.size.pk}">{self.size.name}</option>')


class QualityDefectMasterPageTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="qualityuser", password="pass1234")
        self.user.is_staff = True
        self.user.save()

    def test_quality_dashboard_offers_manual_refresh_and_background_sync(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("quality:dashboard"))

        self.assertContains(response, 'id="dashboard-refresh-button"')
        self.assertContains(response, "Dashboard data syncs every 10 seconds")
        self.assertContains(response, "dashboardSyncIntervalMs = 10000")
        self.assertContains(response, 'id="dashboard-live-content"')
        self.assertContains(response, 'id="dashboard-live-scripts"')
        self.assertNotContains(response, "Auto-refreshing in")

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
        self.size = Size.objects.create(name="Medium")
        self.order = ProductionOrder.objects.create(
            order_number="PO-2001",
            article=self.article,
            order_quantity=500,
            customer_name="Buyer B",
            delivery_date="2026-10-01",
        )
        self.user = get_user_model().objects.create_user(username="checker", password="pass1234")

        now = timezone.localtime()
        start_dt = now - timedelta(minutes=30)
        end_dt = now + timedelta(minutes=30)
        self.shift = FactoryShift.objects.create(
            factory=self.factory,
            shift_name="Current Shift",
            start_time=start_dt.strftime("%H:%M"),
            end_time=end_dt.strftime("%H:%M"),
            shift_minutes=60,
            hourly_schedule=[{
                "hour": 1,
                "name": "Current Hour",
                "start": start_dt.strftime("%H:%M"),
                "end": end_dt.strftime("%H:%M"),
                "start_day": 0,
                "end_day": int(end_dt.date() > start_dt.date()),
                "break_start": "",
                "break_end": "",
            }],
        )
        LineShiftManpower.objects.create(
            factory=self.factory,
            production_line=self.line,
            shift=self.shift,
            entry_date=timezone.localdate(),
            manpower=20,
            entered_by=self.user,
        )

    def test_hourly_check_form_lists_master_sizes(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("quality:hourly_check_create"))

        self.assertContains(response, f'<option value="{self.size.pk}">{self.size.name}</option>')

    def test_hourly_check_cannot_be_recorded_during_a_scheduled_break(self):
        now = timezone.localtime()
        start_dt = now - timedelta(minutes=30)
        end_dt = now + timedelta(minutes=30)
        break_start = now - timedelta(minutes=5)
        break_end = now + timedelta(minutes=5)
        self.shift.hourly_schedule = [{
            "hour": 1,
            "name": "LUNCH BREAK",
            "start": start_dt.strftime("%H:%M"),
            "end": end_dt.strftime("%H:%M"),
            "start_day": 0,
            "end_day": int(end_dt.date() > start_dt.date()),
            "break_start": break_start.strftime("%H:%M"),
            "break_end": break_end.strftime("%H:%M"),
        }]
        self.shift.save()
        LineAssignment.objects.create(
            production_line=self.line,
            production_order=self.order,
            start_date=timezone.localdate(),
            default_manpower=20,
            working_minutes_per_operator=480,
            start_efficiency=Decimal("0.25"),
            daily_efficiency_growth=Decimal("0.05"),
            max_efficiency=Decimal("0.60"),
        )
        self.user.profile.role = UserProfile.Role.QA_PERMISSION
        self.user.profile.factory = self.factory
        self.user.profile.assigned_lines.add(self.line)
        self.user.profile.save()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("quality:hourly_check_create"),
            {
                "production_line": self.line.pk,
                "production_order": self.order.pk,
                "article": self.article.pk,
                "size": self.size.pk,
                "inspected_quantity": "10",
                "passed_quantity": "10",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quality checks cannot be recorded during")
        self.assertFalse(HourlyLineCheck.objects.filter(production_line=self.line).exists())

    def _create_defect_image_detail(self):
        check = HourlyLineCheck.objects.create(
            factory=self.factory,
            production_line=self.line,
            production_order=self.order,
            article=self.article,
            check_date=timezone.localdate(),
            check_hour=9,
            shift_name="Day Shift",
            checker=self.user,
        )
        defect = DefectMaster.objects.create(
            defect_code="D-IMAGE",
            defect_name="Image Test Defect",
        )
        return HourlyLineCheckDetail.objects.create(
            hourly_check=check,
            defect=defect,
            defect_image="quality/defects/test.png",
        )

    def test_defect_image_requires_login_and_allows_assigned_qa(self):
        detail = self._create_defect_image_detail()
        url = reverse(
            "quality_private_defect_image",
            kwargs={"image_path": detail.defect_image.name},
        )

        anonymous_response = self.client.get(url)
        self.assertEqual(anonymous_response.status_code, 302)

        self.user.profile.role = UserProfile.Role.QA_PERMISSION
        self.user.profile.factory = self.factory
        self.user.profile.save()
        self.user.profile.assigned_lines.add(self.line)
        self.client.force_login(self.user)
        with self.settings(DEBUG=False):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["X-Accel-Redirect"],
            "/_protected_media/quality/defects/test.png",
        )
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_defect_image_is_served_by_django_in_debug_mode(self):
        detail = self._create_defect_image_detail()
        self.user.profile.role = UserProfile.Role.QA_PERMISSION
        self.user.profile.factory = self.factory
        self.user.profile.save()
        self.user.profile.assigned_lines.add(self.line)
        self.client.force_login(self.user)

        with TemporaryDirectory() as media_root:
            image_file = Path(media_root) / detail.defect_image.name
            image_file.parent.mkdir(parents=True)
            image_file.write_bytes(b"test image bytes")
            with self.settings(DEBUG=True, MEDIA_ROOT=media_root):
                response = self.client.get(
                    reverse(
                        "quality_private_defect_image",
                        kwargs={"image_path": detail.defect_image.name},
                    )
                )
                body = b"".join(response.streaming_content)
                response.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body, b"test image bytes")
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_defect_image_denies_qa_assigned_to_another_line(self):
        detail = self._create_defect_image_detail()
        another_line = ProductionLine.objects.create(
            factory=self.factory,
            line_name="Line 06",
            capacity_manpower=30,
            default_working_minutes=480,
        )
        self.user.profile.role = UserProfile.Role.QA_PERMISSION
        self.user.profile.factory = self.factory
        self.user.profile.save()
        self.user.profile.assigned_lines.add(another_line)
        self.client.force_login(self.user)

        response = self.client.get(
            reverse(
                "quality_private_defect_image",
                kwargs={"image_path": detail.defect_image.name},
            )
        )

        self.assertEqual(response.status_code, 403)

    def test_defect_image_allows_superuser(self):
        detail = self._create_defect_image_detail()
        superuser = get_user_model().objects.create_superuser(
            username="image-admin",
            email="image-admin@example.com",
            password="strong-test-password",
        )
        self.client.force_login(superuser)

        with self.settings(DEBUG=False):
            response = self.client.get(
                reverse(
                    "quality_private_defect_image",
                    kwargs={"image_path": detail.defect_image.name},
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["X-Accel-Redirect"],
            "/_protected_media/quality/defects/test.png",
        )

    def test_defect_image_rejects_unsupported_file_extensions(self):
        detail = self._create_defect_image_detail()
        detail.defect_image = "quality/defects/attack.html"
        detail.save(update_fields=["defect_image"])
        self.user.profile.role = UserProfile.Role.QA_PERMISSION
        self.user.profile.factory = self.factory
        self.user.profile.save()
        self.user.profile.assigned_lines.add(self.line)
        self.client.force_login(self.user)

        response = self.client.get(
            reverse(
                "quality_private_defect_image",
                kwargs={"image_path": detail.defect_image.name},
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_hourly_check_rejects_non_image_upload_before_creating_check(self):
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
            defect_code="D-BAD-IMAGE",
            defect_name="Invalid Image",
        )
        upload = SimpleUploadedFile(
            "not-an-image.png",
            b"<script>window.pwned = true</script>",
            content_type="image/png",
        )

        response = self.client.post(
            reverse("quality:hourly_check_create"),
            {
                "production_line": self.line.pk,
                "article": self.article.pk,
                "size": self.size.pk,
                "inspected_quantity": "10",
                "passed_quantity": "9",
                "defect_code": defect.defect_code,
                "defect_quantity": "1",
                "repaired_quantity": "0",
                "defect_image": upload,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Defect images must be valid PNG, JPEG, or WebP images")
        self.assertFalse(HourlyLineCheck.objects.filter(production_line=self.line).exists())


    def test_qa_dashboard_article_switch_filters_data_and_assigned_article_choices(self):
        another_article = Article.objects.create(
            article_code="A-201",
            article_name="Another Shirt",
            sam=Decimal("15"),
        )
        another_order = ProductionOrder.objects.create(
            order_number="PO-2002",
            article=another_article,
            order_quantity=800,
            customer_name="Buyer B",
            delivery_date="2026-10-01",
        )
        unassigned_line = ProductionLine.objects.create(
            factory=self.factory,
            line_name="Line 06",
            capacity_manpower=30,
            default_working_minutes=480,
        )
        unassigned_article = Article.objects.create(
            article_code="A-202",
            article_name="Unassigned Line Shirt",
            sam=Decimal("16"),
        )
        unassigned_order = ProductionOrder.objects.create(
            order_number="PO-2003",
            article=unassigned_article,
            order_quantity=600,
            customer_name="Buyer B",
            delivery_date="2026-10-01",
        )
        for line, order in (
            (self.line, self.order),
            (self.line, another_order),
            (unassigned_line, unassigned_order),
        ):
            LineAssignment.objects.create(
                production_line=line,
                production_order=order,
                start_date="2026-09-01",
                default_manpower=30,
                working_minutes_per_operator=480,
                start_efficiency="0.25",
                daily_efficiency_growth="0.05",
                max_efficiency="0.60",
            )
        self.user.profile.role = UserProfile.Role.QA_PERMISSION
        self.user.profile.factory = self.factory
        self.user.profile.save()
        self.user.profile.assigned_lines.add(self.line)
        defect = DefectMaster.objects.create(
            defect_code="D-SWITCH",
            defect_name="Article Switch Defect",
        )
        own_check = HourlyLineCheck.objects.create(
            factory=self.factory,
            production_line=self.line,
            production_order=self.order,
            article=self.article,
            check_date=timezone.localdate(),
            check_hour=9,
            shift_name="Day Shift",
            inspected_quantity=8,
            passed_quantity=5,
            defective_quantity=3,
            checker=self.user,
        )
        own_check.details.create(defect=defect, defect_quantity=3)
        another_check = HourlyLineCheck.objects.create(
            factory=self.factory,
            production_line=self.line,
            production_order=another_order,
            article=another_article,
            check_date=timezone.localdate(),
            check_hour=10,
            shift_name="Day Shift",
            inspected_quantity=12,
            passed_quantity=10,
            defective_quantity=2,
            checker=self.user,
        )
        another_check.details.create(defect=defect, defect_quantity=2)
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("dashboard:qa_dashboard"),
            {"article": another_article.pk},
        )

        self.assertEqual(response.context["qa_shift_inspected"], 12)
        self.assertEqual(response.context["qa_shift_passed"], 10)
        self.assertEqual(len(response.context["hourly_report"]), 1)
        self.assertEqual(response.context["hourly_report"][0]["hour"], 10)
        self.assertEqual(len(response.context["repair_queue"]), 1)
        self.assertEqual(response.context["repair_queue"][0]["article"], another_article.article_code)
        self.assertEqual(response.context["selected_line"], self.line)
        self.assertContains(response, another_article.article_code)
        self.assertNotContains(response, unassigned_article.article_code)

        data_response = self.client.get(
            reverse("dashboard:qa_dashboard_data"),
            {"article": another_article.pk},
        )
        self.assertEqual(data_response.status_code, 200)
        self.assertEqual(data_response.json()["shift_totals"]["inspected"], 12)
        self.assertEqual(len(data_response.json()["hourly_report"]), 1)

        switched_response = self.client.get(
            reverse("dashboard:qa_dashboard"),
            {"article": self.article.pk},
        )

        self.assertEqual(switched_response.context["qa_shift_inspected"], 8)
        self.assertEqual(switched_response.context["qa_shift_passed"], 5)
        self.assertEqual(len(switched_response.context["hourly_report"]), 1)
        self.assertEqual(switched_response.context["hourly_report"][0]["hour"], 9)
        self.assertEqual(switched_response.context["repair_queue"][0]["article"], self.article.article_code)

    def test_qa_actions_reject_articles_assigned_only_to_an_unassigned_line(self):
        unassigned_line = ProductionLine.objects.create(
            factory=self.factory,
            line_name="Line 07",
            capacity_manpower=30,
            default_working_minutes=480,
        )
        unassigned_article = Article.objects.create(
            article_code="A-203",
            article_name="Unassigned Line Article",
            sam=Decimal("17"),
        )
        unassigned_order = ProductionOrder.objects.create(
            order_number="PO-2004",
            article=unassigned_article,
            order_quantity=500,
            customer_name="Buyer B",
            delivery_date="2026-10-01",
        )
        LineAssignment.objects.create(
            production_line=unassigned_line,
            production_order=unassigned_order,
            start_date="2026-09-01",
            default_manpower=30,
            working_minutes_per_operator=480,
            start_efficiency="0.25",
            daily_efficiency_growth="0.05",
            max_efficiency="0.60",
        )
        self.user.profile.role = UserProfile.Role.QA_PERMISSION
        self.user.profile.factory = self.factory
        self.user.profile.save()
        self.user.profile.assigned_lines.add(self.line)
        self.client.force_login(self.user)
        defect = DefectMaster.objects.create(defect_code="D-UNASSIGNED", defect_name="Unassigned")

        pass_response = self.client.post(
            reverse("dashboard:qa_mark_pass"),
            {"line_id": self.line.pk, "article_id": unassigned_article.pk},
        )
        defect_response = self.client.post(
            reverse("dashboard:qa_mark_defect"),
            data=json.dumps({
                "line_id": self.line.pk,
                "article_id": unassigned_article.pk,
                "defect_code": defect.defect_code,
                "quantity": 1,
            }),
            content_type="application/json",
        )

        self.assertEqual(pass_response.status_code, 400)
        self.assertEqual(defect_response.status_code, 400)
        self.assertFalse(HourlyLineCheck.objects.filter(article=unassigned_article).exists())

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
        image_bytes = BytesIO()
        Image.new("RGB", (2, 2), color="red").save(image_bytes, format="JPEG")

        with TemporaryDirectory() as media_root:
            with self.settings(MEDIA_ROOT=media_root):
                response = self.client.post(
                    reverse("quality:hourly_check_create"),
                    {
                        "production_line": self.line.pk,
                        "production_order": self.order.pk,
                        "article": self.article.pk,
                        "size": self.size.pk,
                        "inspected_quantity": "100",
                        "passed_quantity": "95",
                        "defect_code": defect.defect_code,
                        "defect_quantity": "5",
                        "repaired_quantity": "5",
                        "resolved": "on",
                        "defect_image": SimpleUploadedFile(
                            "attacker.html",
                            image_bytes.getvalue(),
                            content_type="text/html",
                        ),
                    },
                )
                detail = HourlyLineCheckDetail.objects.get(defect=defect)
                with detail.defect_image.open("rb") as sanitized_file:
                    sanitized_bytes = sanitized_file.read()

        self.assertRedirects(response, reverse("quality:hourly_check_list"))
        self.assertTrue(detail.defect_image.name.endswith(".png"))
        self.assertTrue(sanitized_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(HourlyLineCheck.objects.filter(production_line=self.line, inspected_quantity=100).exists())
        dashboard_response = self.client.get(reverse("dashboard:qa_dashboard"))
        self.assertEqual(dashboard_response.context["qa_shift_passed"], 100)
        self.assertEqual(dashboard_response.context["qa_shift_defects"], 5)
        self.assertEqual(dashboard_response.context["qa_current_hour_inspected"], 100)
        self.assertEqual(dashboard_response.context["qa_current_hour_passed"], 100)
        check = HourlyLineCheck.objects.get(production_line=self.line, inspected_quantity=100)
        self.assertEqual(check.size, self.size)

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
        self.assertEqual(repair_payload["shift_totals"]["defects"], 4)

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


class DefectImageSanitizationTest(TestCase):
    def test_sanitization_reencodes_valid_image_with_a_safe_png_extension(self):
        image_bytes = BytesIO()
        Image.new("RGB", (2, 2), color="red").save(image_bytes, format="JPEG")
        uploaded_file = SimpleUploadedFile(
            "attacker.html",
            image_bytes.getvalue(),
            content_type="text/html",
        )

        sanitized = sanitize_defect_image(uploaded_file)

        self.assertTrue(sanitized.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(BytesIO(sanitized)) as image:
            self.assertEqual(image.format, "PNG")

    def test_sanitization_rejects_html_disguised_as_png(self):
        uploaded_file = SimpleUploadedFile(
            "attacker.png",
            b"<script>window.pwned = true</script>",
            content_type="image/png",
        )

        with self.assertRaises(ValidationError):
            sanitize_defect_image(uploaded_file)


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
        response = self.client.get(reverse("dashboard:qa_dashboard"), {"article": "7"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Inspection")
        self.assertContains(response, "Line: QA Line 01")
        self.assertContains(response, "Line Hour Inspection Report")
        self.assertContains(response, "Total Pass pcs")
        self.assertContains(response, "Total Inspected pcs")
        self.assertContains(response, "Total Defect pcs")
        self.assertContains(response, "Resolve Pending Defect")
        self.assertContains(response, "Repair")
        self.assertContains(response, 'id="dashboard-refresh-button"')
        self.assertContains(response, "Dashboard data syncs every 10 seconds")
        self.assertContains(response, "dashboardSyncIntervalMs = 10000")
        self.assertContains(response, 'id="dashboard-live-content"')
        self.assertContains(response, 'id="dashboard-live-scripts"')
        self.assertNotContains(response, "Manual refresh only")
        self.assertNotContains(response, "Auto-refreshing in")
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

    def test_factory_shift_supports_custom_hour_schedule(self):
        factory = Factory.objects.create(name="Shift Factory", code="SF1")
        shift = FactoryShift.objects.create(
            factory=factory,
            shift_name="Main Shift",
            start_time="08:00:00",
            end_time="20:00:00",
            shift_minutes=720,
            hourly_schedule=[
                {"hour": 1, "start": "08:00", "end": "20:00", "break_start": "", "break_end": ""},
            ],
            active=True,
        )

        self.assertEqual(shift.scheduled_working_minutes, 720)

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
                "simple_create": "1",
                "shift_name": "Day Shift",
                "start_time": "08:00",
                "end_time": "16:00",
            },
        )

        self.assertEqual(create_response.status_code, 302)
        self.assertTrue(FactoryShift.objects.filter(factory=factory, shift_name="Day Shift").exists())
