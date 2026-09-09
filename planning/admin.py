from django.contrib import admin

from common.admin import FactoryScopedAdminMixin
from .forms import LineAssignmentAdminForm
from .models import LineAssignment, ProductionOrder


@admin.register(ProductionOrder)
class ProductionOrderAdmin(FactoryScopedAdminMixin, admin.ModelAdmin):
    list_display = ("order_number", "article", "sam", "order_quantity", "customer_name", "delivery_date", "status")
    readonly_fields = ("sam",)
    list_filter = ("status",)
    search_fields = ("order_number", "customer_name", "article__article_code")


@admin.register(LineAssignment)
class LineAssignmentAdmin(FactoryScopedAdminMixin, admin.ModelAdmin):
    form = LineAssignmentAdminForm
    list_display = (
        "production_line", "production_order", "start_date",
        "sam", "default_manpower", "status",
    )
    readonly_fields = ("sam",)
    list_filter = ("status", "production_line")
    search_fields = ("production_order__order_number", "production_line__line_name")
