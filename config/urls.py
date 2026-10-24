from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from quality.views import defect_image

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="dashboard:user_dashboard", permanent=False)),
    path("admin/", admin.site.urls),
    path("media/<path:image_path>", defect_image, name="quality_private_defect_image"),
    path("accounts/", include("accounts.urls")),
    path("dashboard/", include("dashboard.urls")),
    path("production/", include("production.urls")),
    path("quality/", include("quality.urls")),
    path("reports/", include("reports.urls")),
]
