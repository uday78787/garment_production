from datetime import date
from decimal import Decimal
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from master_data.models import Article, Factory, ProductionLine, Size
from planning.models import LineAssignment, ProductionOrder


class ArticleAdminTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="article-admin",
            email="article-admin@example.com",
            password="test-password",
        )
        self.client.force_login(self.admin)

    def test_article_and_order_forms_expose_requested_fields(self):
        size = Size.objects.create(name="Medium")
        article_response = self.client.get(reverse("admin:master_data_article_add"))
        for label in (
            "Article code",
            "Article name",
            "Category",
            "Sub-category",
            "Enable quality checklist",
            "Color",
            "Size",
            "Article image",
            "Measurement bulletin",
            "Operation bulletin",
        ):
            self.assertContains(article_response, label)
        self.assertContains(article_response, f">{size.name}</option>")
        self.assertNotContains(article_response, 'name="quality_checklist"')
        self.assertNotContains(article_response, 'name="defect_bulletin"')
        for label, template in (
            ("Measurement bulletin", "measurement_bulletin_sample.csv"),
            ("Operation bulletin", "operation_bulletin_sample.csv"),
        ):
            self.assertContains(article_response, f"Download {label} CSV")
            self.assertContains(article_response, template)
        self.assertContains(article_response, "does not populate structured bulletin rows")

        order_response = self.client.get(reverse("admin:planning_productionorder_add"))
        for label in (
            "PO No. / Order ID",
            "Order Quantity",
            "Client",
            "Merchant",
            "Priority",
            "Order Start Date",
            "Order End Date",
        ):
            self.assertContains(order_response, label)

        assignment_response = self.client.get(reverse("admin:planning_lineassignment_add"))
        self.assertContains(assignment_response, "Assigned Factory")
        self.assertContains(assignment_response, "Operation Start Date")
        self.assertContains(assignment_response, "Operation End Date")

    def test_sizes_are_managed_from_master_data(self):
        response = self.client.get(reverse("admin:master_data_size_changelist"))
        self.assertEqual(response.status_code, 200)

    def test_saved_article_links_to_bulletin_settings(self):
        factory = Factory.objects.create(name="Linked Factory", code="LINKED")
        article = Article.objects.create(
            factory=factory,
            article_code="ART-LINKED",
            article_name="Linked Article",
            sam=Decimal("10"),
        )

        response = self.client.get(
            reverse("admin:master_data_article_change", args=[article.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("admin:master_data_article_bulletin_settings", args=[article.pk]),
        )

    def test_factory_admin_only_sees_and_assigns_articles_for_primary_factory(self):
        factory = Factory.objects.create(name="Article Factory", code="ARTICLE")
        other_factory = Factory.objects.create(name="Other Article Factory", code="OTHER")
        article = Article.objects.create(
            factory=factory,
            article_code="ART-OWN",
            article_name="Own Factory Article",
            sam=Decimal("10"),
        )
        other_article = Article.objects.create(
            factory=other_factory,
            article_code="ART-OTHER",
            article_name="Other Factory Article",
            sam=Decimal("11"),
        )
        factory_admin = get_user_model().objects.create_user(
            username="factory-article-admin",
            password="test-password",
            is_staff=True,
        )
        factory_admin.profile.role = "FACTORY_ADMIN"
        factory_admin.profile.factory = factory
        factory_admin.profile.save()
        factory_admin.profile.accessible_factories.add(other_factory)
        self.client.force_login(factory_admin)

        list_response = self.client.get(reverse("admin:master_data_article_changelist"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, article.article_code)
        self.assertNotContains(list_response, other_article.article_code)
        self.assertContains(list_response, "Bulletin Settings")
        self.assertContains(
            list_response,
            reverse("admin:master_data_article_bulletin_settings", args=[article.pk]),
        )

        add_response = self.client.get(reverse("admin:master_data_article_add"))
        self.assertEqual(add_response.status_code, 200)
        article_form = add_response.context["adminform"].form
        self.assertEqual(
            list(article_form.fields["factory"].queryset),
            [factory],
        )
        self.assertEqual(article_form.initial["factory"], factory.pk)

        other_change_response = self.client.get(
            reverse("admin:master_data_article_change", args=[other_article.pk])
        )
        self.assertEqual(other_change_response.status_code, 302)
        self.assertEqual(other_change_response["Location"], reverse("admin:index"))

        self.client.force_login(self.admin)
        superuser_response = self.client.get(reverse("admin:master_data_article_changelist"))
        self.assertContains(superuser_response, other_article.article_code)

    def test_only_superusers_can_manage_size_catalog(self):
        staff_user = get_user_model().objects.create_user(
            username="size-staff",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse("admin:master_data_size_changelist"))

        self.assertEqual(response.status_code, 403)

    def test_article_files_are_downloadable_by_staff_only(self):
        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                article = Article.objects.create(
                    article_code="ART-FILE",
                    article_name="File Article",
                    sam=Decimal("12.5"),
                    measurement_bulletin=SimpleUploadedFile(
                        "measurement-bulletin.pdf",
                        b"private article file",
                        content_type="application/pdf",
                    ),
                    operation_bulletin=SimpleUploadedFile(
                        "operation-bulletin.csv",
                        b"private operation bulletin",
                        content_type="text/csv",
                    ),
                )
                file_contents = (
                    (article.measurement_bulletin, b"private article file"),
                    (article.operation_bulletin, b"private operation bulletin"),
                )
                self.client.logout()
                for article_file, _ in file_contents:
                    url = reverse(
                        "private_article_file",
                        kwargs={"file_path": article_file.name},
                    )
                    self.assertEqual(self.client.get(url).status_code, 302)

                self.client.force_login(self.admin)
                for article_file, expected_content in file_contents:
                    url = reverse(
                        "private_article_file",
                        kwargs={"file_path": article_file.name},
                    )
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("attachment", response["Content-Disposition"])
                    self.assertEqual(
                        b"".join(response.streaming_content),
                        expected_content,
                    )
                    response.close()

    def test_factory_admin_can_only_download_article_files_for_own_factory(self):
        factory = Factory.objects.create(name="File Factory", code="FILE")
        other_factory = Factory.objects.create(name="Other File Factory", code="OTHERFILE")
        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                article = Article.objects.create(
                    factory=factory,
                    article_code="ART-OWN-FILE",
                    article_name="Own Factory File",
                    sam=Decimal("10"),
                    operation_bulletin=SimpleUploadedFile(
                        "own-operation.csv",
                        b"own operation data",
                        content_type="text/csv",
                    ),
                )
                other_article = Article.objects.create(
                    factory=other_factory,
                    article_code="ART-OTHER-FILE",
                    article_name="Other Factory File",
                    sam=Decimal("10"),
                    operation_bulletin=SimpleUploadedFile(
                        "other-operation.csv",
                        b"other operation data",
                        content_type="text/csv",
                    ),
                )
                factory_admin = get_user_model().objects.create_user(
                    username="article-file-admin",
                    password="test-password",
                    is_staff=True,
                )
                factory_admin.profile.role = "FACTORY_ADMIN"
                factory_admin.profile.factory = factory
                factory_admin.profile.save()
                self.client.force_login(factory_admin)

                own_url = reverse(
                    "private_article_file",
                    kwargs={"file_path": article.operation_bulletin.name},
                )
                other_url = reverse(
                    "private_article_file",
                    kwargs={"file_path": other_article.operation_bulletin.name},
                )
                own_response = self.client.get(own_url)
                self.assertEqual(own_response.status_code, 200)
                self.assertEqual(
                    b"".join(own_response.streaming_content),
                    b"own operation data",
                )
                own_response.close()
                self.assertEqual(self.client.get(other_url).status_code, 404)


class ProductionOrderDateValidationTests(TestCase):
    def setUp(self):
        self.article = Article.objects.create(
            article_code="ART-DATE",
            article_name="Date Article",
            sam=Decimal("12.5"),
        )

    def test_order_end_date_cannot_precede_order_start_date(self):
        order = ProductionOrder(
            order_number="PO-DATE",
            article=self.article,
            order_quantity=100,
            customer_name="Client",
            start_date=date(2026, 8, 2),
            delivery_date=date(2026, 8, 1),
        )
        with self.assertRaises(ValidationError):
            order.full_clean()

    def test_operation_end_date_cannot_precede_operation_start_date(self):
        factory = Factory.objects.create(name="Date Factory", code="DATE")
        line = ProductionLine.objects.create(
            factory=factory,
            line_name="Date Line",
            capacity_manpower=20,
        )
        order = ProductionOrder.objects.create(
            order_number="PO-OP-DATE",
            article=self.article,
            order_quantity=100,
            customer_name="Client",
            delivery_date=date(2026, 8, 10),
        )
        assignment = LineAssignment(
            production_line=line,
            production_order=order,
            start_date=date(2026, 8, 2),
            end_date=date(2026, 8, 1),
            default_manpower=20,
            start_efficiency=Decimal("0.25"),
            daily_efficiency_growth=Decimal("0.05"),
            max_efficiency=Decimal("0.60"),
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()


class ArticleQualityChecklistToggleTests(TestCase):
    def test_quality_checklist_toggle_is_saved_per_article(self):
        article = Article.objects.create(
            article_code="ART-QC",
            article_name="Checklist Article",
            sam=Decimal("12.5"),
            quality_checklist_enabled=True,
        )

        article.refresh_from_db()
        self.assertTrue(article.quality_checklist_enabled)
