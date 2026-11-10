from django.urls import path

from .views import (
    DataSettingsView,
    DataExportView,
    BrandCreateView,
    BrandDetailView,
    BrandListView,
    BrandUpdateView,
    DistributorDashboardView,
    OwnerDashboardView,
    dashboard_redirect,
)


urlpatterns = [
    path("owner/settings/data/", DataSettingsView.as_view(), name="data-settings"),
    path("owner/settings/data/export/", DataExportView.as_view(), name="data-export"),
    path("", dashboard_redirect, name="dashboard"),
    path("owner/", OwnerDashboardView.as_view(), name="owner-dashboard"),
    path("distributor/", DistributorDashboardView.as_view(), name="distributor-dashboard"),
    path("owner/brand/", BrandListView.as_view(), name="brand-list"),
    path("owner/brand/new/", BrandCreateView.as_view(), name="brand-create"),
    path("owner/brand/<uuid:pk>/", BrandDetailView.as_view(), name="brand-detail"),
    path("owner/brand/<uuid:pk>/edit/", BrandUpdateView.as_view(), name="brand-edit"),
]
