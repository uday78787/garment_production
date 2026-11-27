from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import UserProfile
from master_data.models import Article, Factory
from planning.admin import (
    ArticleProcessFlowAdmin,
    FactorySectionAdmin,
    SectionEAVFieldAdmin,
)
from planning.models import (
    ArticleProcessFlow,
    ArticleProcessStep,
    FactorySection,
    OrderProcess,
    ProcessBatch,
    ProcessQualityCheck,
    ProductionOrder,
    SectionEAVField,
)
from production.process_forms import OrderProcessSetupForm, ProcessBatchForm


class FactoryProcessWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.superuser = user_model.objects.create_superuser(
            username="process-root",
            email="process-root@example.com",
            password="test-password",
        )
        self.factory = Factory.objects.create(name="Workflow Factory", code="WF")
        self.article = Article.objects.create(
            factory=self.factory,
            article_code="ART-FLOW",
            article_name="Workflow Article",
            sam=Decimal("12.5"),
        )
        self.order = ProductionOrder.objects.create(
            order_number="PO-FLOW",
            article=self.article,
            order_quantity=1000,
            customer_name="Client",
            delivery_date=date(2026, 12, 31),
        )
        self.sections = [
            FactorySection.objects.create(
                factory=self.factory,
                name=name,
                quality_criteria=f"Check {name} quality.",
            )
            for name in ("Fabric Inhouse", "Embroidery", "Cutting")
        ]
        self.article_flow = ArticleProcessFlow.objects.create(
            article=self.article,
            factory=self.factory,
            created_by=self.superuser,
        )
        self._add_flow_operations(self.article_flow, self.sections)
        self.process = OrderProcess.objects.create(
            factory=self.factory,
            production_order=self.order,
            source_flow=self.article_flow,
            created_by=self.superuser,
        )
        self.process.create_steps(self.article_flow)

    def _add_flow_operations(self, flow, sections):
        for sequence, section in enumerate(sections, start=1):
            ArticleProcessStep.objects.create(
                flow=flow,
                sequence=sequence,
                section=section,
                operation_name=f"{section.name} operation",
                operation_description=f"Complete {section.name}.",
            )

    def _batch(self, step, input_quantity, output_quantity):
        return ProcessBatch.objects.create(
            step=step,
            input_quantity=input_quantity,
            output_quantity=output_quantity,
            entered_by=self.superuser,
        )

    def _quality_check(self, batch, inspected_quantity, passed_quantity):
        return ProcessQualityCheck.objects.create(
            batch=batch,
            inspected_quantity=inspected_quantity,
            passed_quantity=passed_quantity,
            checked_by=self.superuser,
        )

    def test_article_operations_are_snapshotted_in_article_flow_order(self):
        steps = list(self.process.steps.all())

        self.assertEqual(
            [
                (step.sequence, step.section_name, step.operation_name)
                for step in steps
            ],
            [
                (1, "Fabric Inhouse", "Fabric Inhouse operation"),
                (2, "Embroidery", "Embroidery operation"),
                (3, "Cutting", "Cutting operation"),
            ],
        )
        self.assertEqual(steps[0].quality_criteria, "Check Fabric Inhouse quality.")
        self.assertEqual(
            steps[0].operation_details["machine_equipment"],
            "",
        )

    def test_next_section_can_only_consume_quantity_passed_by_previous_qc(self):
        fabric_step, embroidery_step, cutting_step = list(self.process.steps.all())
        fabric_batch = self._batch(fabric_step, 100, 100)

        self.assertEqual(embroidery_step.available_input_quantity, 0)
        with self.assertRaises(ValidationError):
            self._batch(embroidery_step, 1, 1)

        self._quality_check(fabric_batch, inspected_quantity=100, passed_quantity=100)
        self.assertEqual(embroidery_step.available_input_quantity, 100)
        embroidery_batch = self._batch(embroidery_step, 100, 100)
        self._quality_check(embroidery_batch, inspected_quantity=100, passed_quantity=80)

        self.assertEqual(cutting_step.available_input_quantity, 80)
        self.assertEqual(embroidery_batch.quality_checks.first().rejected_quantity, 20)
        self.assertEqual(embroidery_step.available_input_quantity, 0)

    def test_scrapped_output_never_moves_to_the_next_section(self):
        fabric_step, embroidery_step, _ = list(self.process.steps.all())
        fabric_batch = self._batch(fabric_step, 25, 25)
        self._quality_check(fabric_batch, inspected_quantity=25, passed_quantity=0)

        self.assertEqual(embroidery_step.available_input_quantity, 0)
        self.assertEqual(fabric_step.passed_quantity, 0)

    def test_qc_cannot_inspect_more_than_batch_output_or_pass_more_than_inspected(self):
        first_step = self.process.steps.first()
        batch = self._batch(first_step, 10, 8)

        with self.assertRaises(ValidationError):
            self._quality_check(batch, inspected_quantity=9, passed_quantity=8)
        with self.assertRaises(ValidationError):
            self._quality_check(batch, inspected_quantity=8, passed_quantity=9)

    def test_superuser_eav_field_schema_is_captured_and_required_in_batch_form(self):
        field = SectionEAVField.objects.create(
            section=self.sections[0],
            name="Fabric Lot",
            key="fabric_lot",
            field_type=SectionEAVField.FieldType.TEXT,
            required=True,
        )
        eav_article = Article.objects.create(
            factory=self.factory,
            article_code="ART-EAV",
            article_name="EAV Article",
            sam=Decimal("10"),
        )
        eav_flow = ArticleProcessFlow.objects.create(
            article=eav_article,
            factory=self.factory,
            created_by=self.superuser,
        )
        ArticleProcessStep.objects.create(
            flow=eav_flow,
            sequence=1,
            section=self.sections[0],
            operation_name="Cut fabric",
        )
        process = OrderProcess.objects.create(
            factory=self.factory,
            production_order=ProductionOrder.objects.create(
                order_number="PO-EAV",
                article=eav_article,
                order_quantity=50,
                customer_name="Client",
                delivery_date=date(2026, 12, 31),
            ),
            source_flow=eav_flow,
            created_by=self.superuser,
        )
        process.create_steps(process.source_flow)
        step = process.steps.get()
        self.assertEqual(step.field_schema[0]["key"], field.key)

        form_data = {
            "input_quantity": "10",
            "output_quantity": "10",
            "notes": "",
        }
        missing_field_form = ProcessBatchForm(data=form_data, step=step)
        self.assertFalse(missing_field_form.is_valid())
        self.assertIn("field_fabric_lot", missing_field_form.errors)

        form_data["field_fabric_lot"] = "LOT-100"
        valid_form = ProcessBatchForm(data=form_data, step=step)
        self.assertTrue(valid_form.is_valid(), valid_form.errors)
        batch = valid_form.save(commit=False)
        batch.entered_by = self.superuser
        batch.save()
        self.assertEqual(batch.field_values, {"fabric_lot": "LOT-100"})

    def test_order_setup_form_scopes_factories_and_requires_article_flow(self):
        admin_user = get_user_model().objects.create_user(
            username="workflow-admin",
            password="test-password",
            is_staff=True,
        )
        profile = admin_user.profile
        profile.role = UserProfile.Role.FACTORY_ADMIN
        profile.factory = self.factory
        profile.save()
        unassigned_order = ProductionOrder.objects.create(
            order_number="PO-UNASSIGNED",
            article=self.article,
            order_quantity=200,
            customer_name="Client",
            delivery_date=date(2026, 12, 31),
        )

        form = OrderProcessSetupForm(user=admin_user)
        self.assertQuerySetEqual(
            form.fields["factory"].queryset,
            [self.factory],
            transform=lambda factory: factory,
        )
        self.assertIn(unassigned_order, form.fields["production_order"].queryset)
        self.assertNotIn(self.order, form.fields["production_order"].queryset)
        setup_form = OrderProcessSetupForm(
            data={
                "factory": self.factory.pk,
                "production_order": unassigned_order.pk,
            },
            user=admin_user,
        )
        self.assertTrue(setup_form.is_valid(), setup_form.errors)

    def test_only_superusers_can_manage_eav_field_settings(self):
        admin_user = get_user_model().objects.create_user(
            username="section-admin",
            password="test-password",
            is_staff=True,
        )
        profile = admin_user.profile
        profile.role = UserProfile.Role.FACTORY_ADMIN
        profile.factory = self.factory
        profile.save()

        request_factory = RequestFactory()
        section_admin = FactorySectionAdmin(FactorySection, admin.site)
        field_admin = SectionEAVFieldAdmin(SectionEAVField, admin.site)
        request = request_factory.get("/admin/")
        request.user = admin_user

        self.assertTrue(section_admin.has_module_permission(request))
        self.assertTrue(section_admin.get_queryset(request).filter(pk=self.sections[0].pk).exists())
        flow_admin = ArticleProcessFlowAdmin(ArticleProcessFlow, admin.site)
        self.assertTrue(flow_admin.has_module_permission(request))
        self.assertTrue(flow_admin.get_queryset(request).filter(pk=self.article_flow.pk).exists())
        other_factory = Factory.objects.create(name="Scoped Flow Factory", code="SCOPED")
        other_article = Article.objects.create(
            factory=other_factory,
            article_code="ART-SCOPED",
            article_name="Scoped Article",
            sam=Decimal("10"),
        )
        profile.accessible_factories.add(other_factory)
        flow_form = flow_admin.get_form(request)()
        self.assertIn(self.article, flow_form.fields["article"].queryset)
        self.assertNotIn(other_article, flow_form.fields["article"].queryset)
        self.assertQuerySetEqual(
            flow_form.fields["factory"].queryset,
            [self.factory],
            transform=lambda factory: factory,
        )
        self.assertFalse(field_admin.has_module_permission(request))
        self.assertFalse(field_admin.has_add_permission(request))

        self.client.force_login(admin_user)
        section_response = self.client.get(
            reverse("admin:planning_factorysection_changelist")
        )
        self.assertEqual(section_response.status_code, 200)
        self.assertContains(section_response, "Fabric Inhouse")
        self.assertNotContains(section_response, "Sequence")
        flow_response = self.client.get(reverse("admin:planning_articleprocessflow_add"))
        self.assertEqual(flow_response.status_code, 200)
        self.assertContains(flow_response, "Sequence")
        self.assertContains(flow_response, "SAM")
        self.assertContains(flow_response, "Next Operation")
        flow_prefix = flow_response.context["inline_admin_formsets"][0].formset.prefix
        inline_data = {
            f"{flow_prefix}-TOTAL_FORMS": "1",
            f"{flow_prefix}-INITIAL_FORMS": "0",
            f"{flow_prefix}-MIN_NUM_FORMS": "1",
            f"{flow_prefix}-MAX_NUM_FORMS": "1000",
            f"{flow_prefix}-0-id": "",
            f"{flow_prefix}-0-sequence": "1",
            f"{flow_prefix}-0-section": str(self.sections[0].pk),
            f"{flow_prefix}-0-operation_name": "Join shoulder seams",
            f"{flow_prefix}-0-operation_description": "Join front and back panels.",
            f"{flow_prefix}-0-machine_equipment": "Lockstitch",
            f"{flow_prefix}-0-attachment_foot": "Standard foot",
            f"{flow_prefix}-0-standard_time_minutes": "0.45",
            f"{flow_prefix}-0-quality_checkpoint": "Check seam strength.",
            f"{flow_prefix}-0-skill_level": "Skilled",
            f"{flow_prefix}-0-remarks": "",
        }
        new_article = Article.objects.create(
            factory=self.factory,
            article_code="ART-ADMIN-FLOW",
            article_name="Admin Flow Article",
            sam=Decimal("10"),
        )
        inline_data.update(
            {
                "article": str(new_article.pk),
                "factory": str(self.factory.pk),
                "_save": "Save",
            }
        )
        flow_save_response = self.client.post(
            reverse("admin:planning_articleprocessflow_add"),
            inline_data,
        )
        self.assertEqual(flow_save_response.status_code, 302)
        configured_flow = ArticleProcessFlow.objects.get(
            article=new_article,
            factory=self.factory,
        )
        operation = configured_flow.steps.get()
        self.assertEqual(operation.sequence, 1)
        self.assertEqual(operation.operation_name, "Join shoulder seams")

        eav_response = self.client.get(
            reverse("admin:planning_sectioneavfield_changelist")
        )
        self.assertEqual(eav_response.status_code, 403)

        request.user = self.superuser
        self.assertTrue(field_admin.has_module_permission(request))
        self.assertTrue(field_admin.has_add_permission(request))
        self.client.force_login(self.superuser)
        self.assertEqual(
            self.client.get(
                reverse("admin:planning_sectioneavfield_changelist")
            ).status_code,
            200,
        )

    def test_factory_admin_cannot_access_another_factory_process(self):
        other_factory = Factory.objects.create(name="Other Workflow Factory", code="OWF")
        other_article = Article.objects.create(
            factory=other_factory,
            article_code="ART-OTHER-FLOW",
            article_name="Other Factory Article",
            sam=Decimal("10"),
        )
        other_order = ProductionOrder.objects.create(
            order_number="PO-OTHER",
            article=other_article,
            order_quantity=100,
            customer_name="Other Client",
            delivery_date=date(2026, 12, 31),
        )
        other_section = FactorySection.objects.create(
            factory=other_factory,
            name="Dispatch",
            quality_criteria="Check packed quantity and labels.",
        )
        other_flow = ArticleProcessFlow.objects.create(
            article=other_article,
            factory=other_factory,
            created_by=self.superuser,
        )
        ArticleProcessStep.objects.create(
            flow=other_flow,
            sequence=1,
            section=other_section,
            operation_name="Pack order",
        )
        other_process = OrderProcess.objects.create(
            factory=other_factory,
            production_order=other_order,
            source_flow=other_flow,
            created_by=self.superuser,
        )
        other_process.create_steps(other_flow)

        admin_user = get_user_model().objects.create_user(
            username="scoped-admin",
            password="test-password",
            is_staff=True,
        )
        profile = admin_user.profile
        profile.role = UserProfile.Role.FACTORY_ADMIN
        profile.factory = self.factory
        profile.save()
        self.client.force_login(admin_user)

        response = self.client.get(
            reverse("production:process_detail", args=[other_process.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_process_flow_ui_can_be_used_by_factory_qa_and_is_admin_assignable(self):
        qa_user = get_user_model().objects.create_user(
            username="workflow-qa",
            password="test-password",
        )
        qa_user.profile.role = UserProfile.Role.QA_PERMISSION
        qa_user.profile.factory = self.factory
        qa_user.profile.save()
        self.client.force_login(qa_user)

        response = self.client.get(
            reverse("production:process_detail", args=[self.process.pk])
        )
        self.assertEqual(response.status_code, 200)

        fabric_step, embroidery_step, _ = list(self.process.steps.all())
        response = self.client.post(
            reverse(
                "production:process_batch_create",
                args=[self.process.pk, fabric_step.pk],
            ),
            {"input_quantity": "100", "output_quantity": "100", "notes": ""},
        )
        self.assertEqual(response.status_code, 302)
        fabric_batch = fabric_step.batches.get()

        blocked_response = self.client.post(
            reverse(
                "production:process_batch_create",
                args=[self.process.pk, embroidery_step.pk],
            ),
            {"input_quantity": "1", "output_quantity": "1", "notes": ""},
        )
        self.assertEqual(blocked_response.status_code, 200)
        self.assertContains(blocked_response, "Only 0 pcs are available")

        response = self.client.post(
            reverse(
                "production:process_quality_check_create",
                args=[self.process.pk, fabric_batch.pk],
            ),
            {
                "inspected_quantity": "100",
                "passed_quantity": "80",
                "remarks": "20 pcs failed",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(embroidery_step.available_input_quantity, 80)

        self.client.force_login(self.superuser)
        next_order = ProductionOrder.objects.create(
            order_number="PO-SETUP-UI",
            article=self.article,
            order_quantity=30,
            customer_name="Client",
            delivery_date=date(2026, 12, 31),
        )
        response = self.client.post(
            reverse("production:process_setup"),
            {
                "factory": self.factory.pk,
                "production_order": next_order.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        assigned_process = OrderProcess.objects.get(production_order=next_order)
        self.assertEqual(
            list(assigned_process.steps.values_list("operation_name", flat=True)),
            [
                "Fabric Inhouse operation",
                "Embroidery operation",
                "Cutting operation",
            ],
        )
