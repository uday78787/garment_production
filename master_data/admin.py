from django.contrib import admin

from common.admin import FactoryScopedAdminMixin
from .models import Article, Factory, ProductionLine


@admin.register(Factory)
class FactoryAdmin(FactoryScopedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "code", "city", "contact_person", "active_status", "created_at")
    list_filter = ("active_status",)
    search_fields = ("name", "code", "city", "contact_person")

    @admin.display(description="Factory Admin")
    def factory_admin(self, obj):
        admin_profile = obj.users.filter(role="FACTORY_ADMIN").select_related("user").first()
        return admin_profile.user.username if admin_profile else "Pending"

    list_display = ("name", "code", "factory_admin", "city", "contact_person", "active_status", "created_at")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            self.message_user(request, f"Factory Admin account will use a username starting with '{obj.code.lower()}-Admin'. Set its password from Users.")


@admin.register(ProductionLine)
class ProductionLineAdmin(FactoryScopedAdminMixin, admin.ModelAdmin):
    list_display = ("line_name", "factory", "capacity_manpower", "default_working_minutes", "active_status")
    list_filter = ("factory", "active_status")
    search_fields = ("line_name",)


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ("article_code", "article_name", "sam")
    search_fields = ("article_code", "article_name")


