from django.urls import path

from .api import (
    DistributorApproveAPIView,
    DistributorProfileAPIView,
    DistributorRejectAPIView,
    DistributorSuspendAPIView,
)


urlpatterns = [
    path(
        "distributors/<uuid:distributor_id>/approve/",
        DistributorApproveAPIView.as_view(),
        name="api-distributor-approve",
    ),
    path(
        "distributors/<uuid:distributor_id>/suspend/",
        DistributorSuspendAPIView.as_view(),
        name="api-distributor-suspend",
    ),
    path(
        "distributors/<uuid:distributor_id>/reject/",
        DistributorRejectAPIView.as_view(),
        name="api-distributor-reject",
    ),
    path(
        "distributor/profile/",
        DistributorProfileAPIView.as_view(),
        name="api-distributor-profile",
    ),
]
