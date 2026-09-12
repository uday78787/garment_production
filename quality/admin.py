from django.contrib import admin

from .models import DefectMaster, HourlyLineCheck, HourlyLineCheckDetail, QualityHold, QualityInspection


@admin.register(DefectMaster)
class DefectMasterAdmin(admin.ModelAdmin):
    list_display = ("defect_code", "defect_name", "category", "department", "severity", "active")
    list_filter = ("category", "department", "severity", "active")
    search_fields = ("defect_code", "defect_name", "department")


@admin.register(HourlyLineCheck)
class HourlyLineCheckAdmin(admin.ModelAdmin):
    list_display = ("check_date", "production_line", "article", "check_hour", "inspected_quantity", "passed_quantity", "defective_quantity", "checker")
    list_filter = ("check_date", "production_line", "article", "status")
    search_fields = ("production_line__line_name", "article__article_code", "checker__username")


@admin.register(HourlyLineCheckDetail)
class HourlyLineCheckDetailAdmin(admin.ModelAdmin):
    list_display = ("hourly_check", "defect", "defect_quantity")
    list_filter = ("defect__category", "defect__severity")
    search_fields = ("defect__defect_code", "defect__defect_name")


@admin.register(QualityInspection)
class QualityInspectionAdmin(admin.ModelAdmin):
    list_display = ("inspection_number", "inspection_date", "production_line", "article", "inspected_quantity", "passed_quantity", "rejected_quantity", "status")
    list_filter = ("status", "inspection_type", "inspection_date")
    search_fields = ("inspection_number", "article__article_code", "production_line__line_name")


@admin.register(QualityHold)
class QualityHoldAdmin(admin.ModelAdmin):
    list_display = ("hold_number", "production_order", "line", "article", "quantity", "status")
    list_filter = ("status", "severity")
    search_fields = ("hold_number", "production_order__order_number", "article__article_code")
