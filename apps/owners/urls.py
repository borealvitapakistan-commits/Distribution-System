from django.urls import path

from .views import (
    DeactivateOwnerView,
    OwnerCreateView,
    OwnerListView,
    OwnerProfileView,
)


urlpatterns = [
    path("owner/accounts/", OwnerListView.as_view(), name="owner-list"),
    path("owner/accounts/new/", OwnerCreateView.as_view(), name="owner-create"),
    path(
        "owner/accounts/<uuid:user_id>/deactivate/",
        DeactivateOwnerView.as_view(),
        name="deactivate-owner",
    ),
    path("owner/profile/", OwnerProfileView.as_view(), name="owner-profile"),
]
