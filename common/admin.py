from django.contrib import admin

from .models import AuditLog


class FactoryScopedAdminMixin:
    """Keep operational admin records within the user's factory or lines."""

    _factory_scoped_bulletins = {"ArticleMeasurementPoint", "ArticleDefectBulletin"}

    def _can_manage_article_bulletins(self, request):
        profile = getattr(request.user, "profile", None)
        return request.user.is_superuser or bool(
            profile and profile.is_admin and profile.factory_id
        )

    def has_module_permission(self, request):
        if self.model.__name__ in self._factory_scoped_bulletins:
            return self._can_manage_article_bulletins(request)
        return super().has_module_permission(request)

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
            if model_name in {"ArticleMeasurementPoint", "ArticleDefectBulletin"}:
                if profile.factory_id:
                    return queryset.filter(factory_id=profile.factory_id)
                return queryset.none()
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

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return True
        profile = getattr(request.user, "profile", None)
        if profile is None or not profile.is_admin:
            return False
        model_name = self.model.__name__
        if model_name in self._factory_scoped_bulletins:
            return bool(profile.factory_id)
        return model_name != "Factory"

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        profile = getattr(request.user, "profile", None)
        if profile is None:
            return False
        if obj is None:
            if self.model.__name__ in self._factory_scoped_bulletins:
                return bool(profile.is_admin and profile.factory_id)
            return bool(profile.is_admin)
        if profile.is_admin:
            model_name = self.model.__name__
            if model_name == "Factory":
                return obj.pk in profile.accessible_factories.values_list("pk", flat=True)
            if model_name in {"ArticleMeasurementPoint", "ArticleDefectBulletin"}:
                return bool(profile.factory_id and obj.factory_id == profile.factory_id)
            if model_name == "ProductionLine":
                return obj.factory_id in profile.accessible_factories.values_list("pk", flat=True)
            if model_name == "ProductionOrder":
                return obj.assignments.filter(production_line__factory__in=profile.accessible_factories.all()).exists()
            if model_name == "LineAssignment":
                return obj.production_line.factory_id in profile.accessible_factories.values_list("pk", flat=True)
            if model_name == "DailyProductionEntry":
                return obj.assignment.production_line.factory_id in profile.accessible_factories.values_list("pk", flat=True)
        return False

    def has_change_permission(self, request, obj=None):
        if self.model.__name__ in self._factory_scoped_bulletins:
            if obj is None:
                return self._can_manage_article_bulletins(request)
            return self.has_view_permission(request, obj) and self.get_queryset(
                request
            ).filter(pk=obj.pk).exists()
        if obj is None:
            return super().has_change_permission(request, obj)
        return super().has_change_permission(request, obj) and self.get_queryset(request).filter(pk=obj.pk).exists()

    def has_delete_permission(self, request, obj=None):
        if self.model.__name__ in self._factory_scoped_bulletins:
            if obj is None:
                return self._can_manage_article_bulletins(request)
            return self.has_view_permission(request, obj) and self.get_queryset(
                request
            ).filter(pk=obj.pk).exists()
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
