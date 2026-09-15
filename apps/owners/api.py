from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsOwner
from apps.accounts.serializers import UserSerializer

from .models import OwnerProfile
from .serializers import OwnerProfileSerializer, OwnerProfileUpdateSerializer


class OwnerListAPIView(APIView):
    permission_classes = [IsOwner]

    def get(self, request):
        queryset = OwnerProfile.objects.for_user(request.user).select_related("user")
        return Response(OwnerProfileSerializer(queryset, many=True).data)


class OwnerProfileAPIView(APIView):
    """Self-service get/patch for the logged-in Owner's own account."""

    permission_classes = [IsOwner]

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        from .services import update_owner_profile

        serializer = OwnerProfileUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            update_owner_profile(user=request.user, **serializer.validated_data)
        except (DjangoPermissionDenied, DjangoValidationError) as exc:
            if isinstance(exc, DjangoPermissionDenied):
                raise PermissionDenied(str(exc)) from exc
            raise ValidationError(
                getattr(exc, "message_dict", None) or exc.messages
            ) from exc
        return Response(UserSerializer(request.user).data)
