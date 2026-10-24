from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings

from accounts.models import UserProfile
from master_data.models import (
    Article,
    Factory,
    FactoryShift,
    LineShiftManpower,
    ProductionLine,
)
from planning.models import LineAssignment, ProductionOrder
from production.models import DailyProductionEntry
from quality.models import (
    DefectMaster,
    HourlyLineCheck,
    HourlyLineCheckDetail,
    InspectionDefect,
    QualityInspection,
)


class SetupDemoFactoryCommandTests(TransactionTestCase):
    @override_settings(DEBUG=True)
    def test_resets_data_preserves_superusers_and_creates_demo_ecosystem(self):
        user_model = get_user_model()
        superuser = user_model.objects.create_superuser(
            username="preserved-admin",
            email="admin@example.com",
            password="original-password",
        )
        user_model.objects.create_user(
            username="removed-user",
            email="user@example.com",
            password="user-password",
        )
        Factory.objects.create(name="Existing Factory", code="EXISTING")

        call_command("setup_demo_factory", verbosity=0)
        call_command("setup_demo_factory", verbosity=0)

        self.assertEqual(
            list(user_model.objects.values_list("username", flat=True)),
            ["preserved-admin"],
        )
        preserved_superuser = user_model.objects.get(pk=superuser.pk)
        self.assertTrue(preserved_superuser.check_password("original-password"))
        self.assertTrue(preserved_superuser.is_superuser)
        self.assertEqual(
            preserved_superuser.profile.role,
            UserProfile.Role.SUPERUSER,
        )

        self.assertEqual(Factory.objects.count(), 1)
        self.assertEqual(FactoryShift.objects.count(), 3)
        self.assertEqual(ProductionLine.objects.count(), 2)
        self.assertEqual(LineShiftManpower.objects.count(), 2)
        self.assertEqual(Article.objects.count(), 1)
        self.assertEqual(ProductionOrder.objects.count(), 1)
        self.assertEqual(LineAssignment.objects.count(), 2)
        self.assertEqual(DailyProductionEntry.objects.count(), 6)
        self.assertEqual(DefectMaster.objects.count(), 7)
        self.assertEqual(HourlyLineCheck.objects.count(), 2)
        self.assertEqual(HourlyLineCheckDetail.objects.count(), 2)
        self.assertEqual(QualityInspection.objects.count(), 2)
        self.assertEqual(InspectionDefect.objects.count(), 2)

    @override_settings(DEBUG=True)
    def test_setup_requires_superuser_before_changing_data(self):
        factory = Factory.objects.create(name="Keep This Factory", code="KEEP")

        with self.assertRaisesMessage(CommandError, "Create a Superuser"):
            call_command("setup_demo_factory", verbosity=0)

        self.assertTrue(Factory.objects.filter(pk=factory.pk).exists())
        self.assertEqual(get_user_model().objects.count(), 0)

    @override_settings(DEBUG=False)
    def test_setup_refuses_to_reset_when_debug_is_disabled(self):
        Factory.objects.create(name="Keep This Factory", code="KEEP")
        with self.assertRaises(CommandError):
            call_command("setup_demo_factory", verbosity=0)

        self.assertTrue(Factory.objects.filter(code="KEEP").exists())

    def test_creating_a_factory_does_not_create_a_staff_account(self):
        Factory.objects.create(name="User-Free Factory", code="USERFREE")

        self.assertEqual(get_user_model().objects.count(), 0)
