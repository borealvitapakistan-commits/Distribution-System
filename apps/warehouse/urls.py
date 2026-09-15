from django.urls import path

from .views import (
    LocationCreateView,
    LocationDetailView,
    LocationListView,
    LocationUpdateView,
)


urlpatterns = [
    path(
        "owner/warehouse/locations/",
        LocationListView.as_view(),
        name="location-list",
    ),
    path(
        "owner/warehouse/locations/new/",
        LocationCreateView.as_view(),
        name="location-create",
    ),
    path(
        "owner/warehouse/locations/<uuid:pk>/",
        LocationDetailView.as_view(),
        name="location-detail",
    ),
    path(
        "owner/warehouse/locations/<uuid:pk>/edit/",
        LocationUpdateView.as_view(),
        name="location-edit",
    ),
]
