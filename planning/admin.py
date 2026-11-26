from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from common.admin import FactoryScopedAdminMixin
from .forms import LineAssignmentAdminForm
from .models import (
    ArticleProcessFlow,
    ArticleProcessStep,
    FactorySection,
    LineAssignment,
    ProductionOrder,
    SectionEAVField,
)


@admin.register(ProductionOrder)
class ProductionOrderAdmin(FactoryScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "order_number", "article", "sam", "order_quantity", "customer_name",
        "merchant", "priority", "delivery_date", "status",
    )
    readonly_fields = ("sam",)
    list_filter = ("status", "priority")
    search_fields = ("order_number", "customer_name", "merchant", "article__article_code")
    fieldsets = (
        ("Order Details", {
            "fields": (
                "order_number",
                "article",
                "order_quantity",
                "customer_name",
                "merchant",
                "priority",
                "start_date",
                "delivery_date",
                "status",
                "sam",
            ),
        }),
    )


@admin.register(LineAssignment)
class LineAssignmentAdmin(FactoryScopedAdminMixin, admin.ModelAdmin):
    form = LineAssignmentAdminForm
    list_display = (
        "production_line", "assigned_factory", "production_order", "start_date", "end_date",
        "sam", "default_manpower", "status",
    )
    readonly_fields = ("sam", "assigned_factory")
    list_filter = ("status", "production_line")
    search_fields = ("production_order__order_number", "production_line__line_name")
    fieldsets = (
        ("Assignment", {
            "fields": (
                "production_order",
                "production_line",
                "assigned_factory",
                "start_date",
                "end_date",
                "status",
            ),
        }),
        ("Production Planning", {
            "fields": (
                "default_manpower",
                "working_minutes_per_operator",
                "start_efficiency",
                "daily_efficiency_growth",
                "max_efficiency",
                "forecast_efficiency_override",
                "forecast_manpower_override",
                "sam",
            ),
        }),
    )

    @admin.display(description="Assigned Factory")
    def assigned_factory(self, obj):
        return obj.production_line.factory if obj.production_line_id else "-"


@admin.register(FactorySection)
class FactorySectionAdmin(admin.ModelAdmin):
    list_display = ("factory", "name", "active", "updated_at")
    list_filter = ("factory", "active")
    search_fields = ("factory__name", "name")
    ordering = ("factory__name", "name")

    def _is_manager(self, request):
        profile = getattr(request.user, "profile", None)
        return request.user.is_superuser or bool(profile and profile.is_admin)

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related("factory")
        if request.user.is_superuser:
            return queryset
        profile = getattr(request.user, "profile", None)
        if profile and profile.is_admin:
            factory_ids = profile.accessible_factories.values_list("pk", flat=True)
            if profile.factory_id:
                factory_ids = list(factory_ids) + [profile.factory_id]
            return queryset.filter(factory_id__in=factory_ids).distinct()
        return queryset.none()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "factory" and not request.user.is_superuser:
            profile = getattr(request.user, "profile", None)
            if profile and profile.is_admin:
                factory_ids = profile.accessible_factories.values_list("pk", flat=True)
                if profile.factory_id:
                    factory_ids = list(factory_ids) + [profile.factory_id]
                kwargs["queryset"] = db_field.remote_field.model.objects.filter(
                    pk__in=factory_ids
                ).distinct()
            else:
                kwargs["queryset"] = db_field.remote_field.model.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_module_permission(self, request):
        return self._is_manager(request)

    def has_add_permission(self, request):
        return self._is_manager(request)

    def has_view_permission(self, request, obj=None):
        if not self._is_manager(request):
            return False
        if obj is None or obj.pk is None or request.user.is_superuser:
            return True
        return self.get_queryset(request).filter(pk=obj.pk).exists()

    def has_change_permission(self, request, obj=None):
        if not self._is_manager(request):
            return False
        return obj is None or self.has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if not self._is_manager(request):
            return False
        return obj is None or self.has_view_permission(request, obj)


@admin.register(SectionEAVField)
class SectionEAVFieldAdmin(admin.ModelAdmin):
    list_display = ("section", "name", "key", "field_type", "required", "active")
    list_filter = ("section__factory", "section", "field_type", "active")
    search_fields = ("name", "key", "section__name", "section__factory__name")
    ordering = ("section__factory__name", "section__name", "name")

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class ArticleProcessStepInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        factory_id = self.data.get("factory") or self.instance.factory_id
        try:
            factory_id = int(factory_id)
        except (TypeError, ValueError):
            return
        sequences = set()
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            sequence = form.cleaned_data.get("sequence")
            if sequence in sequences:
                raise ValidationError("Use a different sequence number for each Article operation.")
            if sequence is not None:
                sequences.add(sequence)
            section = form.cleaned_data.get("section")
            if section and section.factory_id != factory_id:
                raise ValidationError(
                    "Every operation must use a section belonging to the selected factory."
                )


class ArticleProcessStepInline(admin.TabularInline):
    model = ArticleProcessStep
    formset = ArticleProcessStepInlineFormSet
    extra = 1
    min_num = 1
    validate_min = True
    fields = (
        "sequence",
        "section",
        "operation_name",
        "operation_description",
        "machine_equipment",
        "attachment_foot",
        "standard_time_minutes",
        "quality_checkpoint",
        "skill_level",
        "remarks",
    )

    def _is_manager(self, request):
        profile = getattr(request.user, "profile", None)
        return request.user.is_superuser or bool(profile and profile.is_admin)

    def get_queryset(self, request):
        return super().get_queryset(request).order_by("sequence")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "section":
            if request.user.is_superuser:
                kwargs["queryset"] = FactorySection.objects.filter(active=True)
            else:
                profile = getattr(request.user, "profile", None)
                if profile and profile.is_admin:
                    factory_ids = profile.accessible_factories.values_list("pk", flat=True)
                    if profile.factory_id:
                        factory_ids = list(factory_ids) + [profile.factory_id]
                    kwargs["queryset"] = FactorySection.objects.filter(
                        factory_id__in=factory_ids,
                        active=True,
                    ).distinct()
                else:
                    kwargs["queryset"] = FactorySection.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_add_permission(self, request, obj=None):
        return self._is_manager(request)

    def has_change_permission(self, request, obj=None):
        if not self._is_manager(request):
            return False
        if obj is None or obj.pk is None or request.user.is_superuser:
            return True
        return ArticleProcessFlowAdmin(ArticleProcessFlow, admin.site).get_queryset(
            request
        ).filter(pk=obj.pk).exists()

    def has_delete_permission(self, request, obj=None):
        return self.has_change_permission(request, obj)

    def has_view_permission(self, request, obj=None):
        return self.has_change_permission(request, obj)


@admin.register(ArticleProcessFlow)
class ArticleProcessFlowAdmin(admin.ModelAdmin):
    list_display = ("article", "factory", "operation_count", "updated_at")
    list_filter = ("factory",)
    search_fields = ("article__article_code", "article__article_name", "factory__name")
    inlines = (ArticleProcessStepInline,)
    readonly_fields = ("created_by",)

    def _is_manager(self, request):
        profile = getattr(request.user, "profile", None)
        return request.user.is_superuser or bool(profile and profile.is_admin)

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related("article", "factory")
        if request.user.is_superuser:
            return queryset
        profile = getattr(request.user, "profile", None)
        if profile and profile.is_admin:
            factory_ids = profile.accessible_factories.values_list("pk", flat=True)
            if profile.factory_id:
                factory_ids = list(factory_ids) + [profile.factory_id]
            return queryset.filter(factory_id__in=factory_ids).distinct()
        return queryset.none()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "factory" and not request.user.is_superuser:
            profile = getattr(request.user, "profile", None)
            if profile and profile.is_admin:
                factory_ids = profile.accessible_factories.values_list("pk", flat=True)
                if profile.factory_id:
                    factory_ids = list(factory_ids) + [profile.factory_id]
                kwargs["queryset"] = db_field.remote_field.model.objects.filter(
                    pk__in=factory_ids
                ).distinct()
            else:
                kwargs["queryset"] = db_field.remote_field.model.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        for field in ("article", "factory"):
            if request.GET.get(field):
                initial[field] = request.GET[field]
        return initial

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Operations")
    def operation_count(self, obj):
        return obj.steps.count()

    def has_module_permission(self, request):
        return self._is_manager(request)

    def has_add_permission(self, request):
        return self._is_manager(request)

    def has_view_permission(self, request, obj=None):
        if not self._is_manager(request):
            return False
        if obj is None or request.user.is_superuser:
            return True
        return self.get_queryset(request).filter(pk=obj.pk).exists()

    def has_change_permission(self, request, obj=None):
        if not self._is_manager(request):
            return False
        return obj is None or self.has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if not self._is_manager(request):
            return False
        return obj is None or self.has_view_permission(request, obj)
