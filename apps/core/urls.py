from django.urls import path

from .views import (
    CompanyProfileView,
    DistributorDashboardView,
    DocumentSequenceListView,
    FiscalPeriodListView,
    OwnerDashboardView,
    dashboard_redirect,
)


urlpatterns = [
    path("",dashboard_redirect, name="dashboard"),
    path("owner/", OwnerDashboardView.as_view(), name="owner-dashboard"),
    path("distributor/", DistributorDashboardView.as_view(), name="distributor-dashboard"),
    path("owner/company/", CompanyProfileView.as_view(), name="company-profile"),
    path("owner/fiscal-periods/", FiscalPeriodListView.as_view(), name="fiscal-period-list"),
    path("owner/document-sequences/", DocumentSequenceListView.as_view(), name="document-sequence-list"),
]