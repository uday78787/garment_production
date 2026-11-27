import csv
import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.finders import find
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
from master_data.article_bulletins import (
    export_article_bulletin,
    import_article_bulletin,
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
        self.factory = Factory.objects.create(name="Bulletin Factory", code="BF")
        self.other_factory = Factory.objects.create(name="Other Factory", code="OF")
        self.article = Article.objects.create(
            factory=self.factory,
            article_code="ART-BULLETIN",
            article_name="Bulletin Article",
            sam=Decimal("12.5"),
        )
        self.other_article = Article.objects.create(
            factory=self.other_factory,
            article_code="ART-OTHER-BULLETIN",
            article_name="Other Factory Article",
            sam=Decimal("10"),
        )
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
        for bulletin in response.context["bulletin_cards"]:
            for url_key in ("manual_add_url", "manual_list_url", "export_url"):
                linked_response = self.client.get(bulletin[url_key])
                self.assertEqual(linked_response.status_code, 200, bulletin[url_key])
        for label in (
            "Measurement Bulletin",
            "Operation Bulletin",
            "Defect Bulletin",
            "Add manually",
            "Import and replace",
            "Download sample CSV",
        ):
            self.assertContains(response, label)
        for sample in (
            "templates/article_bulletins/measurement_bulletin_sample.csv",
            "templates/article_bulletins/operation_bulletin_sample.csv",
            "templates/article_bulletins/defect_bulletin_sample.csv",
        ):
            self.assertIsNotNone(find(sample), sample)

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
        ArticleMeasurementPoint.objects.create(
            article=self.article,
            factory=self.factory,
            pom="OWN",
            target_measurement=Decimal("10"),
        )
        ArticleMeasurementPoint.objects.create(
            article=self.other_article,
            factory=self.factory,
            pom="OTHER",
            target_measurement=Decimal("20"),
        )
        ArticleProcessFlow.objects.create(
            article=self.article,
            factory=self.factory,
            created_by=self.admin,
        )
        ArticleProcessFlow.objects.create(
            article=self.other_article,
            factory=self.factory,
            created_by=self.admin,
        )
        ArticleDefectBulletin.objects.create(
            article=self.article,
            factory=self.factory,
            defect_code="OWN",
            defect_name="Own Article Defect",
        )
        ArticleDefectBulletin.objects.create(
            article=self.other_article,
            factory=self.factory,
            defect_code="OTHER",
            defect_name="Other Article Defect",
        )
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
            self.assertEqual(response.context["cl"].queryset.count(), 1)

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
            "Sequence,Department/Section,Operation,Next Operation,SAM (Minutes),"
            "Operation Description,Machine/Equipment,Attachment/Foot,"
            "Quality Checkpoint,Skill Level,Remarks\n"
            "1,Stitching,Join shoulder,Topstitch collar,0.45,Join front and back,"
            "Lockstitch,,Seam strength,Skilled,First operation\n"
            "2,Stitching,Topstitch collar,,0.30,Topstitch the collar,Lockstitch,,"
            "Check alignment,Skilled,Second operation\n",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(flow.steps.values_list("operation_name", flat=True)),
            ["Join shoulder", "Topstitch collar"],
        )
        first_step = flow.steps.first()
        self.assertEqual(first_step.standard_time_minutes, Decimal("0.45"))
        self.assertEqual(first_step.next_operation, "Topstitch collar")
        self.assertEqual(flow.steps.last().next_operation, "")
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

    def test_operation_csv_rejects_a_next_operation_that_conflicts_with_sequence(self):
        flow = ArticleProcessFlow.objects.create(
            article=self.article,
            factory=self.factory,
            created_by=self.admin,
        )
        original = ArticleProcessStep.objects.create(
            flow=flow,
            sequence=1,
            section=self.section,
            operation_name="Keep this operation",
        )

        response = self._import_csv(
            "operation",
            "Sequence,Department/Section,Operation,Next Operation,SAM\n"
            "1,Stitching,Join shoulder,Wrong operation,0.45\n"
            "2,Stitching,Topstitch collar,,0.30\n",
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(ArticleProcessStep.objects.filter(pk=original.pk).exists())
        self.assertEqual(flow.steps.count(), 1)

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

    def test_operation_csv_exports_sam_and_sequence_derived_next_operation(self):
        flow = ArticleProcessFlow.objects.create(
            article=self.article,
            factory=self.factory,
            created_by=self.admin,
        )
        ArticleProcessStep.objects.create(
            flow=flow,
            sequence=1,
            section=self.section,
            operation_name="Join shoulder",
            standard_time_minutes=Decimal("0.45"),
        )
        ArticleProcessStep.objects.create(
            flow=flow,
            sequence=2,
            section=self.section,
            operation_name="Topstitch collar",
            standard_time_minutes=Decimal("0.30"),
        )

        filename, content = export_article_bulletin(
            "operation",
            self.article,
            self.factory,
        )
        rows = list(csv.DictReader(io.StringIO(content)))
        self.assertEqual(rows[0]["SAM (Minutes)"], "0.45")
        self.assertEqual(rows[0]["Next Operation"], "Topstitch collar")
        self.assertEqual(rows[1]["SAM (Minutes)"], "0.30")
        self.assertEqual(rows[1]["Next Operation"], "")

        import_article_bulletin(
            "operation",
            SimpleUploadedFile(
                filename,
                content.encode("utf-8"),
                content_type="text/csv",
            ),
            self.article,
            self.factory,
            self.admin,
        )
        first_step = flow.steps.first()
        self.assertEqual(first_step.standard_time_minutes, Decimal("0.45"))
        self.assertEqual(first_step.next_operation, "Topstitch collar")

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
        profile.accessible_factories.add(self.other_factory)
        own_measurement = ArticleMeasurementPoint.objects.create(
            article=self.article,
            factory=self.factory,
            pom="OWN",
            target_measurement=Decimal("10"),
        )
        other_measurement = ArticleMeasurementPoint.objects.create(
            article=self.other_article,
            factory=self.other_factory,
            pom="OTHER",
            target_measurement=Decimal("20"),
        )
        own_defect = ArticleDefectBulletin.objects.create(
            article=self.article,
            factory=self.factory,
            defect_code="OWN",
            defect_name="Own Factory Defect",
        )
        ArticleDefectBulletin.objects.create(
            article=self.other_article,
            factory=self.other_factory,
            defect_code="OTHER",
            defect_name="Other Factory Defect",
        )
        self.client.force_login(factory_admin)

        response = self.client.get(
            self.settings_url,
            {"factory": self.factory.pk},
        )
        self.assertEqual(response.status_code, 200)

        measurement_list = self.client.get(
            reverse("admin:master_data_articlemeasurementpoint_changelist")
        )
        self.assertEqual(
            list(measurement_list.context["cl"].queryset),
            [own_measurement],
        )
        self.assertNotContains(measurement_list, other_measurement.pom)
        defect_list = self.client.get(
            reverse("admin:quality_articledefectbulletin_changelist")
        )
        self.assertEqual(list(defect_list.context["cl"].queryset), [own_defect])
        self.assertNotContains(defect_list, "Other Factory Defect")

        response = self.client.get(
            reverse("admin:master_data_articlemeasurementpoint_add"),
            {"article": self.article.pk, "factory": self.other_factory.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            self.article,
            response.context["adminform"].form.fields["article"].queryset,
        )
        self.assertNotIn(
            self.other_article,
            response.context["adminform"].form.fields["article"].queryset,
        )
        self.assertEqual(
            list(response.context["adminform"].form.fields["factory"].queryset),
            [self.factory],
        )

        defect_response = self.client.get(
            reverse("admin:quality_articledefectbulletin_add"),
            {"article": self.article.pk, "factory": self.other_factory.pk},
        )
        self.assertEqual(defect_response.status_code, 200)
        self.assertIn(
            self.article,
            defect_response.context["adminform"].form.fields["article"].queryset,
        )
        self.assertNotIn(
            self.other_article,
            defect_response.context["adminform"].form.fields["article"].queryset,
        )
        self.assertEqual(
            list(defect_response.context["adminform"].form.fields["factory"].queryset),
            [self.factory],
        )

        response = self.client.get(
            self.settings_url,
            {"factory": self.other_factory.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected_factory"])
