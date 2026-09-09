from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.ProductionReportView.as_view(), name="production_report"),
    path("export/excel/", views.export_excel, name="export_excel"),
    path("export/pdf/", views.export_pdf, name="export_pdf"),
]
