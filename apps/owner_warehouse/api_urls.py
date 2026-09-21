from django.urls import path

from .api import (
    LocationDetailAPIView,
    LocationListCreateAPIView,
)


urlpatterns = [
    path(
        "locations/",
        LocationListCreateAPIView.as_view(),
        name="api-location-list",
    ),
    path(
        "locations/<uuid:location_id>/",
        LocationDetailAPIView.as_view(),
        name="api-location-detail",
    ),
]
