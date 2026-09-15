from django.urls import path

from .api import (
    DistributorStockBalanceListAPIView,
    GiveToDistributorAPIView,
    ReceiveStockAPIView,
    StockBalanceListAPIView,
)


urlpatterns = [
    path(
        "inventory/balances/",
        StockBalanceListAPIView.as_view(),
        name="api-inventory-balance-list",
    ),
    path(
        "inventory/receive/",
        ReceiveStockAPIView.as_view(),
        name="api-inventory-receive",
    ),
    path(
        "inventory/give/",
        GiveToDistributorAPIView.as_view(),
        name="api-inventory-give",
    ),
    path(
        "distributor/inventory/balances/",
        DistributorStockBalanceListAPIView.as_view(),
        name="api-distributor-inventory-balance-list",
    ),
]
