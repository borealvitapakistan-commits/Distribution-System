from django.urls import path

from .views import (
    DistributorAgreementDeclineView,
    DistributorAgreementDetailView,
    DistributorAgreementListView,
    DistributorAgreementSignView,
    OwnerAgreementCancelView,
    OwnerAgreementCreateView,
    OwnerAgreementDetailView,
    OwnerAgreementListView,
)


urlpatterns = [
    path(
        "owner/agreements/",
        OwnerAgreementListView.as_view(),
        name="owner-agreement-list",
    ),
    path(
        "owner/agreements/new/",
        OwnerAgreementCreateView.as_view(),
        name="owner-agreement-create",
    ),
    path(
        "owner/agreements/<uuid:pk>/",
        OwnerAgreementDetailView.as_view(),
        name="owner-agreement-detail",
    ),
    path(
        "owner/agreements/<uuid:pk>/cancel/",
        OwnerAgreementCancelView.as_view(),
        name="owner-agreement-cancel",
    ),
    path(
        "distributor/agreements/",
        DistributorAgreementListView.as_view(),
        name="distributor-agreement-list",
    ),
    path(
        "distributor/agreements/<uuid:pk>/",
        DistributorAgreementDetailView.as_view(),
        name="distributor-agreement-detail",
    ),
    path(
        "distributor/agreements/<uuid:pk>/sign/",
        DistributorAgreementSignView.as_view(),
        name="distributor-agreement-sign",
    ),
    path(
        "distributor/agreements/<uuid:pk>/decline/",
        DistributorAgreementDeclineView.as_view(),
        name="distributor-agreement-decline",
    ),
]
