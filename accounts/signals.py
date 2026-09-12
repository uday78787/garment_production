"""
accounts/signals.py

Keeps things consistent automatically:
1. Every new User gets a UserProfile (defaulting to Production User).
2. Whenever a profile's role is saved, the user's Django Group membership
   is kept in sync, so `permission_required` / group-based checks and our
   own `profile.role` checks never disagree.
"""

from django.conf import settings
from django.contrib.auth.models import Group
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile

ROLE_GROUP_NAMES = (
    "Superuser",
    "Factory Admin",
    "Supervisor",
    "Dashboard Viewer",
    "QA Permission",
)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=UserProfile)
def sync_group_with_role(sender, instance, **kwargs):
    user = instance.user
    groups = {name: Group.objects.get_or_create(name=name)[0] for name in ROLE_GROUP_NAMES}
    all_role_groups = list(groups.values())
    user.groups.remove(*all_role_groups)

    if user.is_superuser:
        user.groups.add(groups["Superuser"])
        if instance.factory_id:
            instance.accessible_factories.add(instance.factory_id)
        return

    selected_group = {
        UserProfile.Role.FACTORY_ADMIN: groups["Factory Admin"],
        UserProfile.Role.DASHBOARD_VIEWER: groups["Dashboard Viewer"],
        UserProfile.Role.SUPERVISOR: groups["Supervisor"],
        UserProfile.Role.QA_PERMISSION: groups["QA Permission"],
    }.get(instance.role)
    if selected_group:
        user.groups.add(selected_group)
    if instance.factory_id:
        instance.accessible_factories.add(instance.factory_id)
