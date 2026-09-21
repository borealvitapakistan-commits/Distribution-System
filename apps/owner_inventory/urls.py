from django.urls import path

from .views import (
    GiveToDistributorView,
    ReceiveStockView,
    StockBalanceListView,
    StockBatchListView,
    StockMovementListView,
)


urlpatterns = [
    path("owner/inventory/balances/", StockBalanceListView.as_view(), name="stock-balance-list"),
    path("owner/inventory/add/", ReceiveStockView.as_view(), name="inventory-add"),
    path("owner/inventory/give/", GiveToDistributorView.as_view(), name="inventory-give"),
    path("owner/inventory/batches/", StockBatchListView.as_view(), name="stock-batch-list"),
    path(
        "owner/records/movements/",
        StockMovementListView.as_view(),
        name="stock-movement-list",
    ),
]
