from django.contrib import admin

from common.admin import FactoryScopedAdminMixin
from master_data.models import Article, Factory
from .models import (
    ArticleDefectBulletin,
    DefectMaster,
    HourlyLineCheck,
    HourlyLineCheckDetail,
    QualityHold,
    QualityInspection,
)
from .permissions import quality_scope


@admin.register(ArticleDefectBulletin)
class ArticleDefectBulletinAdmin(FactoryScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "article",
        "factory",
        "defect_code",
        "defect_name",
        "category",
        "operation",
        "severity",
    )
    list_filter = ("factory", "category", "severity")
    search_fields = (
        "article__article_code",
        "article__article_name",
        "defect_code",
        "defect_name",
        "operation",
    )
    list_select_related = ("article", "factory")

    def get_list_filter(self, request):
        fields = list(super().get_list_filter(request))
        if not request.user.is_superuser:
            fields.remove("factory")
        return fields

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        for field in ("article", "factory"):
            if request.GET.get(field):
                initial[field] = request.GET[field]
        profile = getattr(request.user, "profile", None)
        if profile and profile.is_admin and profile.factory_id:
            initial["factory"] = profile.factory_id
        return initial

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name in {"article", "factory"} and not request.user.is_superuser:
            profile = getattr(request.user, "profile", None)
            if profile and profile.is_admin:
                if db_field.name == "factory" and profile.factory_id:
                    kwargs["queryset"] = Factory.objects.filter(pk=profile.factory_id)
                elif db_field.name == "article" and profile.factory_id:
                    kwargs["queryset"] = Article.objects.filter(
                        factory_id=profile.factory_id
                    )
                else:
                    kwargs["queryset"] = db_field.remote_field.model.objects.none()
            else:
                kwargs["queryset"] = db_field.remote_field.model.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class QualityFactoryScopedAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        _, lines, _, _ = quality_scope(request.user)
        if self.model is HourlyLineCheck:
            return queryset.filter(production_line__in=lines)
        if self.model is HourlyLineCheckDetail:
            return queryset.filter(hourly_check__production_line__in=lines)
        if self.model is QualityInspection:
            return queryset.filter(production_line__in=lines)
        if self.model is QualityHold:
            return queryset.filter(line__in=lines)
        return queryset


@admin.register(DefectMaster)
class DefectMasterAdmin(admin.ModelAdmin):
    list_display = ("defect_code", "defect_name", "category", "department", "severity", "active")
    list_filter = ("category", "department", "severity", "active")
    search_fields = ("defect_code", "defect_name", "department")


@admin.register(HourlyLineCheck)
class HourlyLineCheckAdmin(QualityFactoryScopedAdmin):
    list_display = ("check_date", "production_line", "article", "size", "check_hour", "inspected_quantity", "passed_quantity", "defective_quantity", "checker")
    list_filter = ("check_date", "production_line", "article", "size", "status")
    list_select_related = ("size",)
    search_fields = ("production_line__line_name", "article__article_code", "checker__username")


@admin.register(HourlyLineCheckDetail)
class HourlyLineCheckDetailAdmin(QualityFactoryScopedAdmin):
    list_display = ("hourly_check", "defect", "defect_quantity")
    list_filter = ("defect__category", "defect__severity")
    search_fields = ("defect__defect_code", "defect__defect_name")


@admin.register(QualityInspection)
class QualityInspectionAdmin(QualityFactoryScopedAdmin):
    list_display = ("inspection_number", "inspection_date", "production_line", "article", "size", "inspected_quantity", "passed_quantity", "rejected_quantity", "status")
    list_filter = ("status", "inspection_type", "inspection_date", "size")
    list_select_related = ("size",)
    search_fields = ("inspection_number", "article__article_code", "production_line__line_name")


@admin.register(QualityHold)
class QualityHoldAdmin(QualityFactoryScopedAdmin):
    list_display = ("hold_number", "production_order", "line", "article", "quantity", "status")
    list_filter = ("status", "severity")
    search_fields = ("hold_number", "production_order__order_number", "article__article_code")
