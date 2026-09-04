from rest_framework import generics
from apps.accounts.permissions import IsOwner
from .models import AuditEvent
from .serializers import AuditEventSerializer


class AuditEventListAPIView(generics.ListAPIView):
    serializer_class = AuditEventSerializer
    permission_classes = [IsOwner]

    def get_queryset(self):
        return (
            AuditEvent.objects
            .for_user(self.request.user)
            .select_related("actor")
        )