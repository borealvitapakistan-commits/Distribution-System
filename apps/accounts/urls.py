from django.urls import path

from .views import (
    AccountPendingView,
    ApproveDistributorView,
    DeactivateUserView,
    DistributorInviteView,
    DistributorProfileView,
    OwnerUserListView,
)


urlpatterns = [
    path("", OwnerUserListView.as_view(), name="owner-user-list"),
    path("invite/", DistributorInviteView.as_view(), name="distributor-invite"),
    path("<uuid:user_id>/approve/", ApproveDistributorView.as_view(), name="approve-distributor"),
    path("<uuid:user_id>/deactivate/", DeactivateUserView.as_view(), name="deactivate-user"),
    path("pending/", AccountPendingView.as_view(), name="account-pending"),
    path("profile/", DistributorProfileView.as_view(), name="distributor-profile"),
]