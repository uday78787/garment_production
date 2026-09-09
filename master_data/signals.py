from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.text import slugify

from accounts.models import UserProfile

from .models import Factory


def _admin_username(factory):
    prefix = slugify(factory.code) or "factory"
    base = f"{prefix}-Admin"
    username = base[:150]
    User = get_user_model()
    suffix = 2
    while User.objects.filter(username=username).exists():
        suffix_text = f"-{suffix}"
        username = f"{base[:150 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    return username


@receiver(post_save, sender=Factory)
def create_factory_admin(sender, instance, created, **kwargs):
    if not created or UserProfile.objects.filter(
        factory=instance, role=UserProfile.Role.FACTORY_ADMIN
    ).exists():
        return

    User = get_user_model()
    username = _admin_username(instance)
    user = User.objects.create_user(
        username=username,
        email=instance.email,
        is_staff=True,
    )
    user.set_unusable_password()
    user.save(update_fields=("password",))
    profile = user.profile
    profile.factory = instance
    profile.role = UserProfile.Role.FACTORY_ADMIN
    profile.save()
    profile.accessible_factories.add(instance)