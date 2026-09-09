from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

from .models import UserProfile


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    extra = 0
    fields = ("role", "factory", "accessible_factories", "assigned_lines")
    filter_horizontal = ("accessible_factories", "assigned_lines")

    def get_readonly_fields(self, request, obj=None):
        if request.user.is_superuser:
            return ()
        return ("accessible_factories",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "factory" and not request.user.is_superuser and hasattr(request.user, "profile"):
            kwargs["queryset"] = request.user.profile.accessible_factories.all()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if not request.user.is_superuser and hasattr(request.user, "profile"):
            if db_field.name == "accessible_factories":
                kwargs["queryset"] = request.user.profile.accessible_factories.all()
            if db_field.name == "assigned_lines":
                kwargs["queryset"] = request.user.profile.assigned_lines.all()
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        if not request.user.is_superuser and hasattr(request.user, "profile") and request.user.profile.is_admin:
            admin_factory = request.user.profile.factory
            for inline_form in formset.forms:
                if inline_form.cleaned_data and not inline_form.cleaned_data.get("DELETE"):
                    profile = inline_form.instance
                    if admin_factory:
                        profile.factory = admin_factory
                        profile.accessible_factories.set(request.user.profile.accessible_factories.all())
                    profile.save()


admin.site.unregister(User)


@admin.register(User)
class UserWithLocationAdmin(UserAdmin):
    inlines = (UserProfileInline,)
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("username", "password1", "password2", "is_staff", "is_active")}),
    )

    def get_fieldsets(self, request, obj=None):
        if request.user.is_superuser:
            return super().get_fieldsets(request, obj)
        return (
            (None, {"fields": ("username", "password")}),
            ("Personal info", {"fields": ("first_name", "last_name", "email")}),
            ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser")}),
        )

    def get_readonly_fields(self, request, obj=None):
        if request.user.is_superuser:
            return super().get_readonly_fields(request, obj)
        return ("is_superuser", "groups", "user_permissions")

    def has_add_permission(self, request):
        return request.user.is_superuser or bool(getattr(request.user, "profile", None) and request.user.profile.is_admin)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "factory")
    list_filter = ("role", "factory")
    search_fields = ("user__username", "user__email")
    filter_horizontal = ("accessible_factories", "assigned_lines")

    def get_readonly_fields(self, request, obj=None):
        return () if request.user.is_superuser else ("accessible_factories",)
