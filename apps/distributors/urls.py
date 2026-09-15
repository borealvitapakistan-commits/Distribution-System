from django.urls import path

from .views import (
    ApproveDistributorView,
    DistributorInviteView,
    DistributorListView,
    DistributorProfileView,
    RejectDistributorView,
    SuspendDistributorView,
)


urlpatterns = [
    path(
        "owner/distributors/",
        DistributorListView.as_view(),
        name="distributor-list",
    ),
    path(
        "owner/distributors/new/",
        DistributorInviteView.as_view(),
        name="distributor-invite",
    ),
    path(
        "owner/distributors/<uuid:user_id>/approve/",
        ApproveDistributorView.as_view(),
        name="approve-distributor",
    ),
    path(
        "owner/distributors/<uuid:user_id>/suspend/",
        SuspendDistributorView.as_view(),
        name="suspend-distributor",
    ),
    path(
        "owner/distributors/<uuid:user_id>/reject/",
        RejectDistributorView.as_view(),
        name="reject-distributor",
    ),
    path(
        "distributor/profile/",
        DistributorProfileView.as_view(),
        name="distributor-profile",
    ),
]
