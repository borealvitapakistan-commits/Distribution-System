from django.urls import path

from .views import (
    BrandCreateView,
    BrandDetailView,
    BrandListView,
    BrandUpdateView,
    DistributorDashboardView,
    OwnerDashboardView,
    dashboard_redirect,
)


urlpatterns = [
    path("", dashboard_redirect, name="dashboard"),
    path("owner/", OwnerDashboardView.as_view(), name="owner-dashboard"),
    path("distributor/", DistributorDashboardView.as_view(), name="distributor-dashboard"),
    path("owner/brand/", BrandListView.as_view(), name="brand-list"),
    path("owner/brand/new/", BrandCreateView.as_view(), name="brand-create"),
    path("owner/brand/<uuid:pk>/", BrandDetailView.as_view(), name="brand-detail"),
    path("owner/brand/<uuid:pk>/edit/", BrandUpdateView.as_view(), name="brand-edit"),
]
