from django.urls import path

from .api import CustomerDetailAPIView, CustomerListCreateAPIView


urlpatterns = [
    path("customers/", CustomerListCreateAPIView.as_view(), name="api-customer-list"),
    path(
        "customers/<uuid:customer_id>/",
        CustomerDetailAPIView.as_view(),
        name="api-customer-detail",
    ),
]
