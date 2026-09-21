from django.urls import path

from .api import DistributorStockBalanceListAPIView


urlpatterns = [
    path(
        "distributor/inventory/balances/",
        DistributorStockBalanceListAPIView.as_view(),
        name="api-distributor-inventory-balance-list",
    ),
]
