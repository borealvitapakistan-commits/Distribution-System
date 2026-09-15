from django.urls import path

from .api import OwnerListAPIView, OwnerProfileAPIView


urlpatterns = [
    path("owners/", OwnerListAPIView.as_view(), name="api-owner-list"),
    path("owner/profile/", OwnerProfileAPIView.as_view(), name="api-owner-profile"),
]
