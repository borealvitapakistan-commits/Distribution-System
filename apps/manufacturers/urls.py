from django.urls import path

from .views import (
    ConfirmPurchaseOrderView,
    ManufacturerOrderEditView,
    ManufacturerOrderQuoteView,
    ManufacturerCreateView,
    ManufacturerDetailView,
    ManufacturerListView,
    ManufacturerOrderCreateView,
    ManufacturerOrderDetailView,
    ManufacturerOrderListView,
    ManufacturerOrderPDFView,
    ManufacturerUpdateView,
    ManufacturerOrderAdvanceView,
    ManufacturerOrderReceiveView,
    RecordManufacturerPaymentView,
    SetManufacturerOrderOutcomeView,
)


urlpatterns = [
    path("owner/manufacturers/", ManufacturerListView.as_view(), name="manufacturer-list"),
    path("owner/manufacturers/new/", ManufacturerCreateView.as_view(), name="manufacturer-create"),
    path("owner/manufacturers/<uuid:pk>/", ManufacturerDetailView.as_view(), name="manufacturer-detail"),
    path("owner/manufacturers/<uuid:pk>/edit/", ManufacturerUpdateView.as_view(), name="manufacturer-edit"),
    path(
        "owner/manufacturer-orders/",
        ManufacturerOrderListView.as_view(),
        name="manufacturer-order-list",
    ),
    path(
        "owner/manufacturer-orders/new/",
        ManufacturerOrderCreateView.as_view(),
        name="manufacturer-order-create",
    ),
    path(
        "owner/manufacturer-orders/<uuid:pk>/",
        ManufacturerOrderDetailView.as_view(),
        name="manufacturer-order-detail",
    ),
    path(
        "owner/manufacturer-orders/<uuid:pk>/edit/",
        ManufacturerOrderEditView.as_view(),
        name="manufacturer-order-edit",
    ),
    path(
        "owner/manufacturer-orders/<uuid:pk>/quote/",
        ManufacturerOrderQuoteView.as_view(),
        name="manufacturer-order-quote",
    ),
    path(
        "owner/manufacturer-orders/<uuid:pk>/confirm/",
        ConfirmPurchaseOrderView.as_view(),
        name="manufacturer-order-confirm",
    ),
    path(
        "owner/manufacturer-orders/<uuid:pk>/received/",
        ManufacturerOrderReceiveView.as_view(),
        name="manufacturer-order-received",
    ),
    path(
        "owner/manufacturer-orders/<uuid:pk>/advance/",
        ManufacturerOrderAdvanceView.as_view(),
        name="manufacturer-order-advance",
    ),
    path(
        "owner/manufacturer-orders/<uuid:pk>/outcome/",
        SetManufacturerOrderOutcomeView.as_view(),
        name="manufacturer-order-outcome",
    ),
    path(
        "owner/manufacturer-orders/<uuid:pk>/payments/",
        RecordManufacturerPaymentView.as_view(),
        name="manufacturer-order-payment",
    ),
    path(
        "owner/manufacturer-orders/<uuid:pk>/pdf/",
        ManufacturerOrderPDFView.as_view(),
        name="manufacturer-order-pdf",
    ),
]
