from django.core.exceptions import PermissionDenied


def ensure_quality_access(user):
    if not user.is_authenticated:
        raise PermissionDenied("Authentication required.")
    if user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    if profile is None:
        raise PermissionDenied("Quality access requires a valid user profile.")
    if getattr(profile, "is_admin", False):
        return True
    allowed_roles = {"SUPERVISOR", "FACTORY_ADMIN", "QA_PERMISSION"}
    if profile.role in allowed_roles:
        return True
    raise PermissionDenied("You do not have permission to access quality data.")


def user_can_access_quality_line(user, production_line):
    if user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    if getattr(profile, "is_admin", False):
        return True
    if profile.role == "QA_PERMISSION":
        return production_line in profile.assigned_lines.all()
    return production_line in profile.assigned_lines.all()
