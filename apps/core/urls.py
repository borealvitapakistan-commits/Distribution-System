from django.urls import path

from .views import (
    BrandCreateView,
    BrandDetailView,
    BrandListView,
    BrandUpdateView,
    OwnerDashboardView,
    brand_logo,
    dashboard_redirect,
)


urlpatterns = [
    path("brand/<uuid:pk>/logo/", brand_logo, name="brand-logo"),
    path("", dashboard_redirect, name="dashboard"),
    path("owner/", OwnerDashboardView.as_view(), name="owner-dashboard"),
    path("owner/brand/", BrandListView.as_view(), name="brand-list"),
    path("owner/brand/new/", BrandCreateView.as_view(), name="brand-create"),
    path("owner/brand/<uuid:pk>/", BrandDetailView.as_view(), name="brand-detail"),
    path("owner/brand/<uuid:pk>/edit/", BrandUpdateView.as_view(), name="brand-edit"),
]
