from django.urls import path

from .views import OwnerFinanceView


urlpatterns = [
    path(
        "owner/finance/",
        OwnerFinanceView.as_view(),
        name="owner-finance",
    ),
]
