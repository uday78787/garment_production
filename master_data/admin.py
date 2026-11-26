from urllib.parse import urlencode

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.templatetags.static import static
from django.urls import path, reverse
from django.utils.html import format_html

from common.admin import FactoryScopedAdminMixin
from .article_bulletins import (
    BulletinImportError,
    export_article_bulletin,
    import_article_bulletin,
)
from .forms import ArticleAdminForm
from .models import (
    Article,
    ArticleMeasurementPoint,
    Factory,
    FactoryShift,
    LineShiftManpower,
    ProductionLine,
    ProductionLineShiftAssignment,
    Size,
)


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


@admin.register(FactoryShift)
class FactoryShiftAdmin(FactoryScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "factory",
        "shift_name",
        "start_time",
        "end_time",
        "shift_minutes",
        "active",
        "assigned_by",
    )
    list_filter = ("factory", "active")
    search_fields = ("shift_name", "factory__name")
    readonly_fields = ("shift_minutes",)
    fieldsets = (
        ("Basic Shift", {
            "fields": ("factory", "shift_name", "start_time", "end_time", "active")
        }),
        ("Hourly Schedule", {"fields": ("hourly_schedule", "shift_minutes")}),
        ("Details", {"fields": ("description", "assigned_by")}),
    )


@admin.register(ProductionLineShiftAssignment)
class ProductionLineShiftAssignmentAdmin(FactoryScopedAdminMixin, admin.ModelAdmin):
    list_display = ("assignment_date", "factory", "production_line", "shift", "active", "assigned_by")
    list_filter = ("factory", "assignment_date", "active")
    search_fields = ("production_line__line_name", "shift__shift_name")


@admin.register(LineShiftManpower)
class LineShiftManpowerAdmin(FactoryScopedAdminMixin, admin.ModelAdmin):
    list_display = ("entry_date", "factory", "production_line", "shift", "manpower", "working_minutes", "entered_by")
    list_filter = ("factory", "entry_date", "shift")
    search_fields = ("production_line__line_name", "shift__shift_name", "entered_by__username")
    readonly_fields = ("factory", "working_minutes", "entered_by")


@admin.register(ProductionLine)
class ProductionLineAdmin(FactoryScopedAdminMixin, admin.ModelAdmin):
    list_display = ("line_name", "factory", "capacity_manpower", "default_working_minutes", "active_status")
    list_filter = ("factory", "active_status")
    search_fields = ("line_name",)


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    form = ArticleAdminForm
    list_display = (
        "article_code",
        "article_name",
        "category",
        "color",
        "size",
        "sam",
        "bulletin_settings",
    )
    list_select_related = ("size",)
    list_filter = ("category", "subcategory", "color", "size")
    search_fields = ("article_code", "article_name", "category", "subcategory", "color", "size")
    fieldsets = (
        ("Article Details", {
            "fields": (
                "article_code",
                "article_name",
                "sam",
                "category",
                "subcategory",
                "quality_checklist_enabled",
            ),
        }),
        ("Variant Details", {"fields": ("color", "size")}),
        ("Article Image", {"fields": ("article_image",)}),
        ("Production Bulletins", {
            "fields": (
                "measurement_bulletin",
                "operation_bulletin",
            ),
        }),
    )

    def _is_bulletin_manager(self, request):
        profile = getattr(request.user, "profile", None)
        return request.user.is_superuser or bool(profile and profile.is_admin)

    def _accessible_factories(self, request):
        if request.user.is_superuser:
            return Factory.objects.all()
        profile = getattr(request.user, "profile", None)
        if not profile or not profile.is_admin:
            return Factory.objects.none()
        factory_ids = list(profile.accessible_factories.values_list("pk", flat=True))
        if profile.factory_id:
            factory_ids.append(profile.factory_id)
        return Factory.objects.filter(pk__in=factory_ids).distinct()

    def get_urls(self):
        urls = [
            path(
                "<path:object_id>/bulletins/",
                self.admin_site.admin_view(self.bulletin_settings_view),
                name="master_data_article_bulletin_settings",
            ),
        ]
        return urls + super().get_urls()

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        if self._is_bulletin_manager(request):
            fieldsets.append(("Bulletin Settings", {"fields": ("bulletin_settings_link",)}))
        return fieldsets

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if self._is_bulletin_manager(request):
            readonly_fields.append("bulletin_settings_link")
        return readonly_fields

    def get_list_display(self, request):
        fields = list(super().get_list_display(request))
        if not self._is_bulletin_manager(request):
            fields.remove("bulletin_settings")
        return fields

    @admin.display(description="Bulletins")
    def bulletin_settings(self, obj):
        url = reverse("admin:master_data_article_bulletin_settings", args=(obj.pk,))
        return format_html('<a class="button" href="{}">Bulletin Settings</a>', url)

    @admin.display(description="Article Bulletins")
    def bulletin_settings_link(self, obj):
        if not obj or not obj.pk:
            return "Save this article first to configure its bulletins."
        url = reverse("admin:master_data_article_bulletin_settings", args=(obj.pk,))
        return format_html('<a class="button" href="{}">Open Bulletin Settings</a>', url)

    def bulletin_settings_view(self, request, object_id):
        if not self._is_bulletin_manager(request):
            raise PermissionDenied
        article = get_object_or_404(Article, pk=object_id)
        factories = self._accessible_factories(request)
        export_type = request.GET.get("export")
        if export_type:
            factory = get_object_or_404(factories, pk=request.GET.get("factory"))
            try:
                filename, content = export_article_bulletin(
                    export_type,
                    article,
                    factory,
                )
            except BulletinImportError as exc:
                raise Http404(str(exc)) from exc
            response = HttpResponse(content, content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response

        if request.method == "POST":
            factory = get_object_or_404(
                factories,
                pk=request.POST.get("factory_id"),
            )
            bulletin_type = request.POST.get("bulletin_type", "")
            upload = request.FILES.get("bulletin_file")
            if not request.POST.get("confirm_replace"):
                self.message_user(
                    request,
                    "Confirm the replacement before importing this CSV.",
                    level=messages.ERROR,
                )
            elif not upload:
                self.message_user(
                    request,
                    "Choose a CSV file to import.",
                    level=messages.ERROR,
                )
            else:
                try:
                    imported_count = import_article_bulletin(
                        bulletin_type,
                        upload,
                        article,
                        factory,
                        request.user,
                    )
                except BulletinImportError as exc:
                    self.message_user(request, str(exc), level=messages.ERROR)
                else:
                    self.message_user(
                        request,
                        f"Imported {imported_count} {bulletin_type} bulletin rows "
                        f"for {article.article_code} at {factory.name}.",
                        level=messages.SUCCESS,
                    )
            return redirect(f"{request.path}?factory={factory.pk}")

        selected_factory = factories.filter(
            pk=request.GET.get("factory")
        ).first()
        bulletin_cards = []
        if selected_factory:
            factory_query = {"article__id__exact": article.pk, "factory__id__exact": selected_factory.pk}
            add_query = {"article": article.pk, "factory": selected_factory.pk}
            bulletin_config = (
                (
                    "measurement",
                    "Measurement Bulletin",
                    "master_data_articlemeasurementpoint",
                    "templates/article_bulletins/measurement_bulletin_sample.csv",
                ),
                (
                    "operation",
                    "Operation Bulletin",
                    "planning_articleprocessflow",
                    "templates/article_bulletins/operation_bulletin_sample.csv",
                ),
                (
                    "defect",
                    "Defect Bulletin",
                    "quality_articledefectbulletin",
                    "templates/article_bulletins/defect_bulletin_sample.csv",
                ),
            )
            for bulletin_type, title, model_url_name, sample_path in bulletin_config:
                bulletin_cards.append(
                    {
                        "type": bulletin_type,
                        "title": title,
                        "sample_url": static(sample_path),
                        "manual_add_url": (
                            reverse(f"admin:{model_url_name}_add")
                            + "?"
                            + urlencode(add_query)
                        ),
                        "manual_list_url": (
                            reverse(f"admin:{model_url_name}_changelist")
                            + "?"
                            + urlencode(factory_query)
                        ),
                        "export_url": (
                            f"{request.path}?"
                            + urlencode(
                                {
                                    "factory": selected_factory.pk,
                                    "export": bulletin_type,
                                }
                            )
                        ),
                    }
                )

        context = {
            **self.admin_site.each_context(request),
            "title": f"Bulletin Settings: {article.article_code}",
            "opts": self.model._meta,
            "article": article,
            "factories": factories,
            "selected_factory": selected_factory,
            "bulletin_cards": bulletin_cards,
            "changelist_url": reverse("admin:master_data_article_changelist"),
        }
        return TemplateResponse(
            request,
            "admin/master_data/article/bulletin_settings.html",
            context,
        )


@admin.register(Size)
class SizeAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)

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


@admin.register(ArticleMeasurementPoint)
class ArticleMeasurementPointAdmin(FactoryScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "article",
        "factory",
        "pom",
        "description",
        "size",
        "target_measurement",
        "tolerance",
        "unit",
    )
    list_filter = ("factory", "size")
    search_fields = ("article__article_code", "article__article_name", "pom", "description")
    list_select_related = ("article", "factory", "size")

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        for field in ("article", "factory"):
            if request.GET.get(field):
                initial[field] = request.GET[field]
        return initial

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "factory" and not request.user.is_superuser:
            profile = getattr(request.user, "profile", None)
            if profile and profile.is_admin:
                factory_ids = list(profile.accessible_factories.values_list("pk", flat=True))
                if profile.factory_id:
                    factory_ids.append(profile.factory_id)
                kwargs["queryset"] = Factory.objects.filter(pk__in=factory_ids).distinct()
            else:
                kwargs["queryset"] = Factory.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

