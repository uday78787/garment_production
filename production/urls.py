from django.urls import path

from . import process_views, views

app_name = "production"

urlpatterns = [
    path("", views.DailyProductionEntryListView.as_view(), name="entry_list"),
    path("new/", views.DailyProductionEntryCreateView.as_view(), name="entry_create"),
    path("<int:pk>/edit/", views.DailyProductionEntryUpdateView.as_view(), name="entry_edit"),
    path("<int:pk>/delete/", views.DailyProductionEntryDeleteView.as_view(), name="entry_delete"),
    path("manpower/", views.LineShiftManpowerView.as_view(), name="manpower"),
    path("processes/", process_views.process_list, name="process_list"),
    path("processes/assign/", process_views.process_setup, name="process_setup"),
    path("processes/<int:process_id>/", process_views.process_detail, name="process_detail"),
    path(
        "processes/<int:process_id>/steps/<int:step_id>/batches/new/",
        process_views.process_batch_create,
        name="process_batch_create",
    ),
    path(
        "processes/<int:process_id>/batches/<int:batch_id>/quality/new/",
        process_views.process_quality_check_create,
        name="process_quality_check_create",
    ),
]
