from django.urls import path

from .api import (
    DistributorStockRequestListCreateAPIView,
    OwnerStockRequestDeclineAPIView,
    OwnerStockRequestDetailAPIView,
    OwnerStockRequestItemFulfillAPIView,
    OwnerStockRequestListAPIView,
)


urlpatterns = [
    path(
        "distributor/requests/",
        DistributorStockRequestListCreateAPIView.as_view(),
        name="api-distributor-stock-request-list",
    ),
    path(
        "requests/",
        OwnerStockRequestListAPIView.as_view(),
        name="api-owner-stock-request-list",
    ),
    path(
        "requests/<uuid:request_id>/",
        OwnerStockRequestDetailAPIView.as_view(),
        name="api-owner-stock-request-detail",
    ),
    path(
        "requests/<uuid:request_id>/decline/",
        OwnerStockRequestDeclineAPIView.as_view(),
        name="api-stock-request-decline",
    ),
    path(
        "requests/items/<uuid:item_id>/fulfill/",
        OwnerStockRequestItemFulfillAPIView.as_view(),
        name="api-stock-request-item-fulfill",
    ),
]
