from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("factory-select/", views.FactorySelectorView.as_view(), name="factory_selector"),
    path("admin/", views.AdminDashboardView.as_view(), name="admin_dashboard"),
    path("admin/daily-detail/export/excel/", views.export_daily_detail_excel, name="export_daily_detail_excel"),
    path("admin/daily-detail/export/pdf/", views.export_daily_detail_pdf, name="export_daily_detail_pdf"),
    path("", views.UserDashboardView.as_view(), name="user_dashboard"),
]
