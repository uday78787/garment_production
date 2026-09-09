from django.contrib import admin

from .models import AuditLog


class FactoryScopedAdminMixin:
    """Keep operational admin records within the user's factory or lines."""

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        profile = getattr(request.user, "profile", None)
        if profile is None:
            return queryset.none()
        if profile.is_admin:
            factories = profile.accessible_factories.all()
            model_name = self.model.__name__
            if model_name == "Factory":
                return queryset.filter(pk__in=factories.values("pk"))
            if model_name == "ProductionLine":
                return queryset.filter(factory__in=factories)
            if model_name == "ProductionOrder":
                return queryset.filter(assignments__production_line__factory__in=factories).distinct()
            if model_name == "LineAssignment":
                return queryset.filter(production_line__factory__in=factories)
            if model_name == "DailyProductionEntry":
                return queryset.filter(assignment__production_line__factory__in=factories)
        if self.model.__name__ == "DailyProductionEntry":
            return queryset.filter(assignment__production_line__in=profile.assigned_lines.all())
        return queryset.none()

    def has_change_permission(self, request, obj=None):
        if obj is None:
            return super().has_change_permission(request, obj)
        return super().has_change_permission(request, obj) and self.get_queryset(request).filter(pk=obj.pk).exists()

    def has_delete_permission(self, request, obj=None):
        if obj is None:
            return super().has_delete_permission(request, obj)
        return super().has_delete_permission(request, obj) and self.get_queryset(request).filter(pk=obj.pk).exists()


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action_date", "user", "action", "model_name", "object_id")
    list_filter = ("action", "model_name")
    search_fields = ("model_name", "object_id", "user__username")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
