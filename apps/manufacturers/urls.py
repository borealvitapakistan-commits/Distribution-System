from django.urls import path

from .views import (
    ManufacturerCreateView,
    ManufacturerDetailView,
    ManufacturerListView,
    ManufacturerUpdateView,
)


urlpatterns = [
    path("owner/manufacturers/", ManufacturerListView.as_view(), name="manufacturer-list"),
    path("owner/manufacturers/new/", ManufacturerCreateView.as_view(), name="manufacturer-create"),
    path("owner/manufacturers/<uuid:pk>/", ManufacturerDetailView.as_view(), name="manufacturer-detail"),
    path("owner/manufacturers/<uuid:pk>/edit/", ManufacturerUpdateView.as_view(), name="manufacturer-edit"),
]
