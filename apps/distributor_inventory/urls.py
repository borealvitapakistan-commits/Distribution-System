from django.urls import path

from .views import (
    DistributorReceiveStockView,
    DistributorStockBalanceListView,
    DistributorStockBatchListView,
    DistributorStockMovementListView,
    SubDistributorSaleCreateView,
    SubDistributorSaleDetailView,
    SubDistributorSalePaymentProofView,
    SubDistributorSaleListView,
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
    path(
        "distributor/sales/",
        SubDistributorSaleListView.as_view(),
        name="distributor-sale-list",
    ),
    path(
        "distributor/sales/new/",
        SubDistributorSaleCreateView.as_view(),
        name="distributor-sale-create",
    ),
    path(
        "distributor/sales/<uuid:pk>/",
        SubDistributorSaleDetailView.as_view(),
        name="distributor-sale-detail",
    ),
    path(
        "distributor/sales/<uuid:pk>/payment-proof/",
        SubDistributorSalePaymentProofView.as_view(),
        name="distributor-sale-payment-proof",
    ),
]
