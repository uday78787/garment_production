from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserProfile
from master_data.models import (
    Article,
    ArticleMeasurementPoint,
    Factory,
    Size,
)
from planning.models import ArticleProcessFlow, ArticleProcessStep, FactorySection
from quality.models import ArticleDefectBulletin


class ArticleBulletinSettingsTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="bulletin-admin",
            email="bulletin-admin@example.com",
            password="test-password",
        )
        self.client.force_login(self.admin)
        self.article = Article.objects.create(
            article_code="ART-BULLETIN",
            article_name="Bulletin Article",
            sam=Decimal("12.5"),
        )
        self.factory = Factory.objects.create(name="Bulletin Factory", code="BF")
        self.other_factory = Factory.objects.create(name="Other Factory", code="OF")
        self.size = Size.objects.create(name="M")
        self.section = FactorySection.objects.create(
            factory=self.factory,
            name="Stitching",
            quality_criteria="Check seam quality.",
        )
        self.other_section = FactorySection.objects.create(
            factory=self.other_factory,
            name="Stitching",
            quality_criteria="Check the other factory seam quality.",
        )
        self.settings_url = reverse(
            "admin:master_data_article_bulletin_settings",
            args=(self.article.pk,),
        )

    def _import_csv(self, bulletin_type, contents, factory=None):
        return self.client.post(
            self.settings_url,
            {
                "factory_id": (factory or self.factory).pk,
                "bulletin_type": bulletin_type,
                "confirm_replace": "1",
                "bulletin_file": SimpleUploadedFile(
                    f"{bulletin_type}.csv",
                    contents.encode("utf-8"),
                    content_type="text/csv",
                ),
            },
        )

    def test_settings_page_shows_all_bulletins_for_selected_factory(self):
        response = self.client.get(
            self.settings_url,
            {"factory": self.factory.pk},
        )

        self.assertEqual(response.status_code, 200)
        for label in (
            "Measurement Bulletin",
            "Operation Bulletin",
            "Defect Bulletin",
            "Add manually",
            "Import and replace",
            "Download sample CSV",
        ):
            self.assertContains(response, label)

    def test_manual_bulletin_add_forms_start_with_article_and_factory_selected(self):
        for model_url_name in (
            "master_data_articlemeasurementpoint",
            "planning_articleprocessflow",
            "quality_articledefectbulletin",
        ):
            response = self.client.get(
                reverse(f"admin:{model_url_name}_add"),
                {"article": self.article.pk, "factory": self.factory.pk},
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, f'value="{self.article.pk}" selected')
            self.assertContains(response, f'value="{self.factory.pk}" selected')

    def test_manual_bulletin_lists_open_with_article_and_factory_filters(self):
        for model_url_name in (
            "master_data_articlemeasurementpoint",
            "planning_articleprocessflow",
            "quality_articledefectbulletin",
        ):
            response = self.client.get(
                reverse(f"admin:{model_url_name}_changelist"),
                {
                    "article__id__exact": self.article.pk,
                    "factory__id__exact": self.factory.pk,
                },
            )
            self.assertEqual(response.status_code, 200)

    def test_import_replaces_only_the_selected_article_and_factory_bulletin(self):
        ArticleMeasurementPoint.objects.create(
            article=self.article,
            factory=self.factory,
            pom="OLD",
            target_measurement=Decimal("10"),
        )
        other_measurement = ArticleMeasurementPoint.objects.create(
            article=self.article,
            factory=self.other_factory,
            pom="KEEP",
            target_measurement=Decimal("20"),
        )
        flow = ArticleProcessFlow.objects.create(
            article=self.article,
            factory=self.factory,
            created_by=self.admin,
        )
        ArticleProcessStep.objects.create(
            flow=flow,
            sequence=1,
            section=self.section,
            operation_name="Old operation",
        )
        other_flow = ArticleProcessFlow.objects.create(
            article=self.article,
            factory=self.other_factory,
            created_by=self.admin,
        )
        ArticleProcessStep.objects.create(
            flow=other_flow,
            sequence=1,
            section=self.other_section,
            operation_name="Keep operation",
        )
        ArticleDefectBulletin.objects.create(
            article=self.article,
            factory=self.factory,
            defect_code="OLD",
            defect_name="Old defect",
        )
        other_defect = ArticleDefectBulletin.objects.create(
            article=self.article,
            factory=self.other_factory,
            defect_code="KEEP",
            defect_name="Keep defect",
        )

        response = self._import_csv(
            "measurement",
            "POM,Description,Size,Target Measurement,Tolerance (+/-),Unit,Remark\n"
            "CHEST,Chest width,M,52,1,cm,Measure flat\n",
        )
        self.assertEqual(response.status_code, 302)
        measurement = ArticleMeasurementPoint.objects.get(
            article=self.article,
            factory=self.factory,
        )
        self.assertEqual(measurement.pom, "CHEST")
        self.assertEqual(measurement.target_measurement, Decimal("52"))
        self.assertEqual(measurement.tolerance, Decimal("1"))
        self.assertTrue(
            ArticleMeasurementPoint.objects.filter(pk=other_measurement.pk).exists()
        )

        response = self._import_csv(
            "operation",
            "Sequence,Department/Section,Operation,Operation Description,"
            "Machine/Equipment,Attachment/Foot,Standard Time (Minutes),"
            "Quality Checkpoint,Skill Level,Remarks\n"
            "1,Stitching,Join shoulder,Join front and back,Lockstitch,,0.45,"
            "Seam strength,Skilled,First operation\n",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(flow.steps.values_list("operation_name", flat=True)),
            ["Join shoulder"],
        )
        self.assertEqual(
            list(other_flow.steps.values_list("operation_name", flat=True)),
            ["Keep operation"],
        )

        response = self._import_csv(
            "defect",
            "Defect Code,Defect Name,Category,Operation,Description,Severity,"
            "Identification Method,Prevention / Corrective Action,Reference Photo,Remarks\n"
            "DEF-1,Skipped stitches,Sewing,Join shoulder,Missing stitches,Major,"
            "Visual inspection,Check thread tension,,Check at seam\n",
        )
        self.assertEqual(response.status_code, 302)
        defect = ArticleDefectBulletin.objects.get(
            article=self.article,
            factory=self.factory,
        )
        self.assertEqual(defect.defect_code, "DEF-1")
        self.assertEqual(defect.prevention_corrective_action, "Check thread tension")
        self.assertTrue(ArticleDefectBulletin.objects.filter(pk=other_defect.pk).exists())

    def test_invalid_csv_does_not_delete_existing_rows(self):
        original = ArticleMeasurementPoint.objects.create(
            article=self.article,
            factory=self.factory,
            pom="KEEP",
            target_measurement=Decimal("10"),
        )

        response = self._import_csv(
            "measurement",
            "POM,Target Measurement\nCHEST,not-a-number\n",
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(ArticleMeasurementPoint.objects.filter(pk=original.pk).exists())
        self.assertEqual(
            ArticleMeasurementPoint.objects.filter(
                article=self.article,
                factory=self.factory,
            ).count(),
            1,
        )

    def test_exports_are_factory_scoped_and_downloadable(self):
        ArticleMeasurementPoint.objects.create(
            article=self.article,
            factory=self.factory,
            pom="CHEST",
            target_measurement=Decimal("52"),
        )
        ArticleMeasurementPoint.objects.create(
            article=self.article,
            factory=self.other_factory,
            pom="SLEEVE",
            target_measurement=Decimal("60"),
        )

        response = self.client.get(
            self.settings_url,
            {"factory": self.factory.pk, "export": "measurement"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn(b"CHEST", response.content)
        self.assertNotIn(b"SLEEVE", response.content)

    def test_factory_admin_can_manage_only_assigned_factory_bulletins(self):
        factory_admin = get_user_model().objects.create_user(
            username="bulletin-factory-admin",
            password="test-password",
            is_staff=True,
        )
        profile = factory_admin.profile
        profile.role = UserProfile.Role.FACTORY_ADMIN
        profile.factory = self.factory
        profile.save()
        self.client.force_login(factory_admin)

        response = self.client.get(
            self.settings_url,
            {"factory": self.factory.pk},
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get(
            reverse("admin:master_data_articlemeasurementpoint_add"),
            {"article": self.article.pk, "factory": self.factory.pk},
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get(
            self.settings_url,
            {"factory": self.other_factory.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected_factory"])
