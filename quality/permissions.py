from django.core.exceptions import PermissionDenied
from master_data.models import Article, Factory, ProductionLine
from planning.models import ProductionOrder


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
        return production_line.factory_id in profile.accessible_factories.values_list("id", flat=True)
    if profile.role == "QA_PERMISSION":
        return production_line in profile.assigned_lines.all()
    return production_line in profile.assigned_lines.all()


def quality_scope(user):
    """Return the factories, lines, orders, and articles visible to a user."""
    ensure_quality_access(user)
    if user.is_superuser:
        lines = ProductionLine.objects.filter(active_status=True)
        factories = Factory.objects.filter(active_status=True)
    else:
        profile = user.profile
        if profile.is_admin:
            factories = profile.accessible_factories.filter(active_status=True)
            lines = ProductionLine.objects.filter(factory__in=factories, active_status=True)
        else:
            lines = profile.assigned_lines.filter(active_status=True)
            factories = Factory.objects.filter(pk__in=lines.values("factory_id"), active_status=True)
            if profile.factory_id:
                lines = lines.filter(factory_id=profile.factory_id)
                factories = factories.filter(pk=profile.factory_id)
    orders = ProductionOrder.objects.filter(assignments__production_line__in=lines).distinct()
    articles = Article.objects.filter(orders__assignments__production_line__in=lines).distinct()
    return factories, lines, orders, articles
