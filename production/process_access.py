from django.core.exceptions import PermissionDenied

from master_data.models import Factory
from planning.models import OrderProcess


OPERATION_ROLES = {"SUPERVISOR", "QA_PERMISSION"}


def accessible_factory_ids(user):
    if user.is_superuser:
        return None

    profile = getattr(user, "profile", None)
    if profile is None:
        return set()

    factory_ids = set(profile.accessible_factories.values_list("pk", flat=True))
    if profile.factory_id:
        factory_ids.add(profile.factory_id)
    factory_ids.update(
        profile.assigned_lines.exclude(factory_id=None).values_list("factory_id", flat=True)
    )
    return factory_ids


def manageable_factories(user):
    if user.is_superuser:
        return Factory.objects.all()
    profile = getattr(user, "profile", None)
    if profile is None or not profile.is_admin:
        raise PermissionDenied("Only a superuser or Factory Admin can configure order processes.")
    factory_ids = accessible_factory_ids(user)
    return Factory.objects.filter(pk__in=factory_ids)


def can_manage_processes(user):
    profile = getattr(user, "profile", None)
    return user.is_superuser or bool(profile and profile.is_admin)


def require_process_access(user, factory_id):
    if not user.is_authenticated:
        raise PermissionDenied("Authentication required.")
    if user.is_superuser:
        return
    factory_ids = accessible_factory_ids(user)
    if factory_id not in factory_ids:
        raise PermissionDenied("You do not have access to this factory's process flow.")
    profile = getattr(user, "profile", None)
    if profile and profile.is_admin:
        return
    if not profile or profile.role not in OPERATION_ROLES:
        raise PermissionDenied("You do not have permission to record process operations or quality checks.")


def visible_processes(user):
    processes = OrderProcess.objects.select_related(
        "factory",
        "production_order__article",
    ).prefetch_related("steps")
    factory_ids = accessible_factory_ids(user)
    if factory_ids is None:
        return processes
    return processes.filter(factory_id__in=factory_ids)
