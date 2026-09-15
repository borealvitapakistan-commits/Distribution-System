from django.urls import path

from .views import (
    CustomerCreateView,
    CustomerDetailView,
    CustomerListView,
    CustomerUpdateView,
)


urlpatterns = [
    path("owner/customers/", CustomerListView.as_view(), name="customer-list"),
    path("owner/customers/new/", CustomerCreateView.as_view(), name="customer-create"),
    path("owner/customers/<uuid:pk>/", CustomerDetailView.as_view(), name="customer-detail"),
    path("owner/customers/<uuid:pk>/edit/", CustomerUpdateView.as_view(), name="customer-edit"),
]
