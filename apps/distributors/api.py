from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsDistributor, IsOwner

from .models import DistributorProfile
from .serializers import DistributorProfileSerializer, DistributorProfileUpdateSerializer


def _raise_api_error(exc):
    if isinstance(exc, DjangoPermissionDenied):
        raise PermissionDenied(str(exc)) from exc
    if isinstance(exc, DjangoValidationError):
        detail = getattr(exc, "message_dict", None) or exc.messages
        raise ValidationError(detail) from exc
    raise exc


class DistributorApproveAPIView(APIView):
    permission_classes = [IsOwner]

    def post(self, request, distributor_id):
        from .services import approve_distributor

        try:
            distributor = approve_distributor(
                user=request.user,
                distributor_id=distributor_id,
            )
        except (DjangoPermissionDenied, DjangoValidationError) as exc:
            _raise_api_error(exc)
        return Response(
            {
                "detail": "Distributor approved successfully.",
                "user_id": str(distributor.pk),
            }
        )


class DistributorSuspendAPIView(APIView):
    permission_classes = [IsOwner]

    def post(self, request, distributor_id):
        from .services import suspend_distributor

        reason = request.data.get("reason", "")
        try:
            profile = suspend_distributor(
                user=request.user,
                distributor_id=distributor_id,
                reason=reason,
            )
        except (DjangoPermissionDenied, DjangoValidationError) as exc:
            _raise_api_error(exc)
        return Response(DistributorProfileSerializer(profile).data)


class DistributorRejectAPIView(APIView):
    permission_classes = [IsOwner]

    def post(self, request, distributor_id):
        from .services import reject_distributor

        try:
            profile = reject_distributor(
                user=request.user,
                distributor_id=distributor_id,
                reason=request.data.get("reason", ""),
            )
        except (DjangoPermissionDenied, DjangoValidationError) as exc:
            _raise_api_error(exc)
        return Response(DistributorProfileSerializer(profile).data)


class DistributorProfileAPIView(APIView):
    permission_classes = [IsDistributor]

    def get(self, request):
        profile = (
            DistributorProfile.objects.for_user(request.user)
            .select_related("user", "approved_by")
            .get(user=request.user)
        )
        return Response(DistributorProfileSerializer(profile).data)

    def patch(self, request):
        from .services import update_distributor_profile

        profile = request.user.distributor_profile
        serializer = DistributorProfileUpdateSerializer(
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        try:
            profile = update_distributor_profile(
                user=request.user,
                profile=profile,
                **serializer.validated_data,
            )
        except (DjangoPermissionDenied, DjangoValidationError) as exc:
            _raise_api_error(exc)
        return Response(DistributorProfileSerializer(profile).data)
