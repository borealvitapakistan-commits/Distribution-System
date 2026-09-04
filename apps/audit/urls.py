from django.urls import path
from .views import AuditEventListView


urlpatterns = [
    path("", AuditEventListView.as_view(), name="audit-event-list"),
]