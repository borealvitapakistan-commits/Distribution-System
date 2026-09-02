from django.urls import path
from .views import DistributorDashboardView, OwnerDashboardView, dashboard_redirect
from django.urls import path
from . import views


urlpatterns = [
    path("", views.dashboard_redirect, name="dashboard"),
    path("owner/", views.OwnerDashboardView.as_view(), name="owner-dashboard"),
    path("distributor/", views.DistributorDashboardView.as_view(), name="distributor-dashboard"),
]






