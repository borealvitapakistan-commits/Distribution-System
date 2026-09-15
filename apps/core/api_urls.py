from django.urls import path

from .api import BrandDetailAPIView


urlpatterns = [
    path("brand/", BrandDetailAPIView.as_view(), name="api-brand"),
]
