from django.urls import path

from .api import AuditEventListAPIView


urlpatterns = [
    path("audit-events/", AuditEventListAPIView.as_view(), name="api-audit-event-list"),
]