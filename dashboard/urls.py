from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("factory-select/", views.FactorySelectorView.as_view(), name="factory_selector"),
    path("admin/", views.AdminDashboardView.as_view(), name="admin_dashboard"),
    path("admin/shift-management/", views.ShiftManagementView.as_view(), name="shift_management"),
    path("admin/daily-detail/export/excel/", views.export_daily_detail_excel, name="export_daily_detail_excel"),
    path("admin/daily-detail/export/pdf/", views.export_daily_detail_pdf, name="export_daily_detail_pdf"),
    path("qa/", views.QADashboardView.as_view(), name="qa_dashboard"),
    path("qa/data/", views.qa_dashboard_data, name="qa_dashboard_data"),
    path("qa/mark-pass/", views.qa_mark_pass, name="qa_mark_pass"),
    path("qa/mark-defect/", views.qa_mark_defect, name="qa_mark_defect"),
    path("qa/repair-defect/", views.qa_repair_defect, name="qa_repair_defect"),
    path("", views.UserDashboardView.as_view(), name="user_dashboard"),
]
