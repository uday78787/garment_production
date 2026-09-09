from django.urls import path

from . import views

app_name = "production"

urlpatterns = [
    path("", views.DailyProductionEntryListView.as_view(), name="entry_list"),
    path("new/", views.DailyProductionEntryCreateView.as_view(), name="entry_create"),
    path("<int:pk>/edit/", views.DailyProductionEntryUpdateView.as_view(), name="entry_edit"),
    path("<int:pk>/delete/", views.DailyProductionEntryDeleteView.as_view(), name="entry_delete"),
]
