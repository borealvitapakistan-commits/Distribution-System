from django.urls import path

from .views import (
    BrandProfileView,
    DistributorDashboardView,
    OwnerDashboardView,
    dashboard_redirect,
)


urlpatterns = [
    path("", dashboard_redirect, name="dashboard"),
    path("owner/", OwnerDashboardView.as_view(), name="owner-dashboard"),
    path("distributor/", DistributorDashboardView.as_view(), name="distributor-dashboard"),
    path("owner/brand/", BrandProfileView.as_view(), name="brand-profile"),
]
