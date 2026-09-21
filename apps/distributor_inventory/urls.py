from django.urls import path

from .views import (
    DistributorReceiveStockView,
    DistributorStockBalanceListView,
    DistributorStockBatchListView,
    DistributorStockMovementListView,
)


urlpatterns = [
    path(
        "distributor/inventory/balances/",
        DistributorStockBalanceListView.as_view(),
        name="distributor-stock-balance-list",
    ),
    path(
        "distributor/inventory/batches/",
        DistributorStockBatchListView.as_view(),
        name="distributor-stock-batch-list",
    ),
    path(
        "distributor/inventory/movements/",
        DistributorStockMovementListView.as_view(),
        name="distributor-stock-movement-list",
    ),
    path(
        "distributor/inventory/receive/",
        DistributorReceiveStockView.as_view(),
        name="distributor-stock-receive",
    ),
]
