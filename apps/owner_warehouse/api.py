from django.core.exceptions import (
    PermissionDenied as DjangoPermissionDenied,
    ValidationError as DjangoValidationError,
)
from django.db import IntegrityError

from rest_framework import status
from rest_framework.exceptions import (
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsOwner

from .models import Location
from .serializers import LocationSerializer


def raise_api_error(exc):
    if isinstance(exc, DjangoPermissionDenied):
        raise PermissionDenied(str(exc))

    if isinstance(exc, DjangoValidationError):
        raise ValidationError(
            getattr(exc, "message_dict", None)
            or exc.messages
        )

    if isinstance(exc, IntegrityError):
        raise ValidationError(
            "The record conflicts with an existing record."
        )

    raise exc


class LocationListCreateAPIView(APIView):
    permission_classes = [IsOwner]

    def get(self, request):
        locations = (
            Location.objects
            .for_user(request.user)
            .select_related("manufacturer")
        )

        return Response(
            LocationSerializer(
                locations,
                many=True,
                context={"request": request},
            ).data
        )

    def post(self, request):
        serializer = LocationSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        from .services import create_location

        try:
            location = create_location(
                actor=request.user,
                **serializer.validated_data,
            )
        except (
            DjangoPermissionDenied,
            DjangoValidationError,
            IntegrityError,
        ) as exc:
            raise_api_error(exc)

        return Response(
            LocationSerializer(
                location,
                context={"request": request},
            ).data,
            status=status.HTTP_201_CREATED,
        )


class LocationDetailAPIView(APIView):
    permission_classes = [IsOwner]

    def get_object(self, request, location_id):
        location = (
            Location.objects
            .for_user(request.user)
            .filter(pk=location_id)
            .first()
        )

        if location is None:
            raise NotFound(
                "Location was not found in your permitted scope."
            )

        return location

    def get(self, request, location_id):
        return Response(
            LocationSerializer(
                self.get_object(request, location_id),
                context={"request": request},
            ).data
        )

    def patch(self, request, location_id):
        location = self.get_object(
            request,
            location_id,
        )

        serializer = LocationSerializer(
            location,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        from .services import update_location

        try:
            location = update_location(
                actor=request.user,
                location_id=location.pk,
                **serializer.validated_data,
            )
        except (
            DjangoPermissionDenied,
            DjangoValidationError,
            IntegrityError,
        ) as exc:
            raise_api_error(exc)

        return Response(
            LocationSerializer(
                location,
                context={"request": request},
            ).data
        )
