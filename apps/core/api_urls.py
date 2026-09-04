from django.urls import path

from .api import (
    CompanyDetailAPIView,
    FiscalPeriodCloseAPIView,
    FiscalPeriodListAPIView,
)


urlpatterns = [
    path("company/", CompanyDetailAPIView.as_view(), name="api-company"),
    path("fiscal-periods/", FiscalPeriodListAPIView.as_view(), name="api-fiscal-period-list"),
    path(
        (
            "fiscal-periods/"
            "<uuid:period_id>/close/"
        ),
        FiscalPeriodCloseAPIView.as_view(),
        name="api-fiscal-period-close",
    ),
]