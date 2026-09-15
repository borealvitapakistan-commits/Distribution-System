from django.urls import path

from .api import ManufacturerDetailAPIView, ManufacturerListCreateAPIView


urlpatterns = [
    path("manufacturers/", ManufacturerListCreateAPIView.as_view(), name="api-manufacturer-list"),
    path(
        "manufacturers/<uuid:manufacturer_id>/",
        ManufacturerDetailAPIView.as_view(),
        name="api-manufacturer-detail",
    ),
]
