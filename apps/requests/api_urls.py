from django.urls import path

from .api import (
    DistributorPurchaseOrderItemReceiveAPIView,
    DistributorPurchaseOrderListCreateAPIView,
    OwnerPurchaseOrderDeclineAPIView,
    OwnerPurchaseOrderDetailAPIView,
    OwnerPurchaseOrderItemShipAPIView,
    OwnerPurchaseOrderListAPIView,
)


urlpatterns = [
    path(
        "distributor/purchase-orders/",
        DistributorPurchaseOrderListCreateAPIView.as_view(),
        name="api-distributor-purchase-order-list",
    ),
    path(
        "purchase-orders/items/<uuid:item_id>/receive/",
        DistributorPurchaseOrderItemReceiveAPIView.as_view(),
        name="api-purchase-order-item-receive",
    ),
    path(
        "purchase-orders/",
        OwnerPurchaseOrderListAPIView.as_view(),
        name="api-owner-purchase-order-list",
    ),
    path(
        "purchase-orders/<uuid:purchase_order_id>/",
        OwnerPurchaseOrderDetailAPIView.as_view(),
        name="api-owner-purchase-order-detail",
    ),
    path(
        "purchase-orders/<uuid:purchase_order_id>/decline/",
        OwnerPurchaseOrderDeclineAPIView.as_view(),
        name="api-purchase-order-decline",
    ),
    path(
        "purchase-orders/items/<uuid:item_id>/ship/",
        OwnerPurchaseOrderItemShipAPIView.as_view(),
        name="api-purchase-order-item-ship",
    ),
]
