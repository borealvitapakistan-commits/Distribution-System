from django.urls import path

from .views import (
    DistributorAllocateStockView,
    DistributorInventoryCreateView,
    DistributorInventoryDetailView,
    DistributorInventoryListView,
    DistributorInventoryUpdateView,
    DistributorLocationCreateView,
    DistributorLocationDetailView,
    DistributorLocationListView,
    DistributorLocationUpdateView,
)


urlpatterns = [
    path(
        "distributor/warehouse/regions/",
        DistributorInventoryListView.as_view(),
        name="distributor-region-list",
    ),
    path(
        "distributor/warehouse/regions/new/",
        DistributorInventoryCreateView.as_view(),
        name="distributor-region-create",
    ),
    path(
        "distributor/warehouse/regions/<uuid:pk>/",
        DistributorInventoryDetailView.as_view(),
        name="distributor-region-detail",
    ),
    path(
        "distributor/warehouse/regions/<uuid:pk>/edit/",
        DistributorInventoryUpdateView.as_view(),
        name="distributor-region-edit",
    ),
    path(
        "distributor/warehouse/allocate/<uuid:batch_id>/",
        DistributorAllocateStockView.as_view(),
        name="distributor-warehouse-allocate",
    ),
    path(
        "distributor/warehouse/locations/",
        DistributorLocationListView.as_view(),
        name="distributor-warehouse-list",
    ),
    path(
        "distributor/warehouse/locations/new/",
        DistributorLocationCreateView.as_view(),
        name="distributor-warehouse-create",
    ),
    path(
        "distributor/warehouse/locations/<uuid:pk>/",
        DistributorLocationDetailView.as_view(),
        name="distributor-warehouse-detail",
    ),
    path(
        "distributor/warehouse/locations/<uuid:pk>/edit/",
        DistributorLocationUpdateView.as_view(),
        name="distributor-warehouse-edit",
    ),
]
