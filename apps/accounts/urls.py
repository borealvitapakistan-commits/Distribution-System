from django.urls import path

from .views import AccountPendingView


urlpatterns = [
    path(
        "pending/",
        AccountPendingView.as_view(),
        name="account-pending",
    ),
]
