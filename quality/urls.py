from django.urls import path

from . import views

app_name = "quality"

urlpatterns = [
    path("", views.quality_dashboard, name="dashboard"),
    path("inspections/", views.inspection_list, name="inspection_list"),
    path("inspections/new/", views.inspection_create, name="inspection_create"),
    path("defects/", views.defect_master_list, name="defect_master_list"),
    path("defects/add/", views.add_defect_master, name="add_defect_master"),
    path("hourly-checks/", views.hourly_check_list, name="hourly_check_list"),
    path("hourly-checks/new/", views.hourly_check_create, name="hourly_check_create"),
    path("bulletin/", views.article_defect_bulletin, name="article_defect_bulletin"),
    path("bulletin/export/excel/", views.export_article_defect_bulletin_excel, name="export_article_defect_bulletin_excel"),
    path("bulletin/export/pdf/", views.export_article_defect_bulletin_pdf, name="export_article_defect_bulletin_pdf"),
    path("holds/", views.quality_hold_list, name="quality_hold_list"),
]
