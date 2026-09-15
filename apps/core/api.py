from rest_framework import generics

from apps.accounts.permissions import IsOwner
from apps.audit.services import record_audit_event

from .models import Brand
from .serializers import BrandSerializer


class BrandDetailAPIView(generics.RetrieveUpdateAPIView):
    serializer_class = BrandSerializer
    permission_classes = [IsOwner]

    def get_object(self):
        brand = Brand.objects.for_user(self.request.user).first()

        if brand is None:
            brand = Brand.objects.create(name="My Brand")

        return brand

    def perform_update(self, serializer):
        brand = self.get_object()

        before_data = BrandSerializer(
            brand
        ).data

        updated_brand = serializer.save()

        record_audit_event(
            user=self.request.user,
            action="brand.updated",
            instance=updated_brand,
            before_data=before_data,
            after_data=serializer.data,
            request=self.request,
        )
