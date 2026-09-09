from django.contrib import admin

from common.admin import FactoryScopedAdminMixin
from .models import DailyProductionEntry


@admin.register(DailyProductionEntry)
class DailyProductionEntryAdmin(FactoryScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "assignment", "production_date", "actual_manpower", "working_minutes", "available_minutes",
        "actual_output", "sam", "actual_efficiency_percent", "planned_efficiency_percent", "gain_loss_minutes",
    )
    list_filter = ("assignment__production_line", "production_date")
    search_fields = ("assignment__production_order__order_number",)
    readonly_fields = (
        "produced_minutes", "available_minutes", "actual_efficiency",
        "planned_efficiency", "target_output", "gain_loss_minutes",
        "sam",
    )

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="Actual Efficiency %")
    def actual_efficiency_percent(self, obj):
        return f"{obj.actual_efficiency_percent:.2f}%"

    @admin.display(description="Planned Efficiency %")
    def planned_efficiency_percent(self, obj):
        return f"{obj.planned_efficiency_percent:.2f}%"
