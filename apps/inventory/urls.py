from django.urls import path

from .views import (
    DistributorInventoryListView,
    GiveToDistributorView,
    InventoryListView,
    ReceiveStockView,
    StockMovementListView,
)


urlpatterns = [
    path("owner/inventory/", InventoryListView.as_view(), name="inventory-list"),
    path("owner/inventory/add/", ReceiveStockView.as_view(), name="inventory-add"),
    path("owner/inventory/give/", GiveToDistributorView.as_view(), name="inventory-give"),
    path(
        "owner/records/movements/",
        StockMovementListView.as_view(),
        name="stock-movement-list",
    ),
    path(
        "distributor/inventory/",
        DistributorInventoryListView.as_view(),
        name="distributor-inventory-list",
    ),
]
