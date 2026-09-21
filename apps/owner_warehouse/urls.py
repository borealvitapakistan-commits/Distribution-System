from django.urls import path

from .views import (
    AllocateStockView,
    InventoryCreateView,
    InventoryDetailView,
    InventoryListView,
    InventoryUpdateView,
    LocationCreateView,
    LocationDetailView,
    LocationListView,
    LocationUpdateView,
)


urlpatterns = [
    path("owner/inventory/", InventoryListView.as_view(), name="inventory-list"),
    path("owner/inventory/new/", InventoryCreateView.as_view(), name="inventory-create"),
    path("owner/inventory/<uuid:pk>/", InventoryDetailView.as_view(), name="inventory-detail"),
    path(
        "owner/inventory/<uuid:pk>/edit/",
        InventoryUpdateView.as_view(),
        name="inventory-edit",
    ),
    path(
        "owner/inventory/allocate/<uuid:batch_id>/",
        AllocateStockView.as_view(),
        name="inventory-allocate",
    ),
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
