from django.urls import path

from .views import (
    CommentStockRequestView,
    DeclineStockRequestView,
    DistributorStockRequestListView,
    FulfillStockRequestItemView,
    OwnerStockRequestDetailView,
    OwnerStockRequestListView,
    StockRequestCreateView,
)


urlpatterns = [
    path(
        "distributor/requests/",
        DistributorStockRequestListView.as_view(),
        name="distributor-stock-request-list",
    ),
    path(
        "distributor/requests/new/",
        StockRequestCreateView.as_view(),
        name="stock-request-create",
    ),
    path(
        "owner/requests/",
        OwnerStockRequestListView.as_view(),
        name="owner-stock-request-list",
    ),
    path(
        "owner/requests/<uuid:pk>/",
        OwnerStockRequestDetailView.as_view(),
        name="owner-stock-request-detail",
    ),
    path(
        "owner/requests/<uuid:pk>/items/<uuid:item_id>/fulfill/",
        FulfillStockRequestItemView.as_view(),
        name="stock-request-item-fulfill",
    ),
    path(
        "owner/requests/<uuid:pk>/decline/",
        DeclineStockRequestView.as_view(),
        name="stock-request-decline",
    ),
    path(
        "owner/requests/<uuid:pk>/comment/",
        CommentStockRequestView.as_view(),
        name="stock-request-comment",
    ),
]
