from django.urls import path

from .api import (
    LoginAPIView,
    LogoutAPIView,
    MeAPIView,
    PasswordResetAPIView,
)


urlpatterns = [
    path("login/", LoginAPIView.as_view(), name="api-login"),
    path("logout/", LogoutAPIView.as_view(), name="api-logout"),
    path("password/reset/", PasswordResetAPIView.as_view(), name="api-password-reset"),
    path("me/", MeAPIView.as_view(), name="api-me"),
]