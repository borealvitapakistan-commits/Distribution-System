from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError

from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsOwner

from .models import Manufacturer
from .serializers import ManufacturerSerializer


def _raise_api_error(exc):
    if isinstance(exc, DjangoPermissionDenied):
        raise PermissionDenied(str(exc)) from exc
    if isinstance(exc, DjangoValidationError):
        detail = getattr(exc, "message_dict", None) or exc.messages
        raise ValidationError(detail) from exc
    if isinstance(exc, IntegrityError):
        raise ValidationError(
            "The record conflicts with an existing record."
        ) from exc
    raise exc


def _get_scoped_manufacturer(request, manufacturer_id):
    manufacturer = (
        Manufacturer.objects.for_user(request.user)
        .filter(pk=manufacturer_id)
        .first()
    )
    if manufacturer is None:
        raise NotFound("Manufacturer was not found in your permitted scope.")
    return manufacturer


class ManufacturerListCreateAPIView(APIView):
    permission_classes = [IsOwner]

    def get(self, request):
        queryset = Manufacturer.objects.for_user(request.user)
        return Response(
            ManufacturerSerializer(queryset, many=True, context={"request": request}).data
        )

    def post(self, request):
        serializer = ManufacturerSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        try:
            manufacturer = serializer.save()
        except (DjangoPermissionDenied, DjangoValidationError, IntegrityError) as exc:
            _raise_api_error(exc)
        return Response(
            ManufacturerSerializer(manufacturer, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class ManufacturerDetailAPIView(APIView):
    permission_classes = [IsOwner]

    def get(self, request, manufacturer_id):
        manufacturer = _get_scoped_manufacturer(request, manufacturer_id)
        return Response(
            ManufacturerSerializer(manufacturer, context={"request": request}).data
        )

    def patch(self, request, manufacturer_id):
        manufacturer = _get_scoped_manufacturer(request, manufacturer_id)
        serializer = ManufacturerSerializer(
            manufacturer,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        try:
            manufacturer = serializer.save()
        except (DjangoPermissionDenied, DjangoValidationError, IntegrityError) as exc:
            _raise_api_error(exc)
        return Response(
            ManufacturerSerializer(manufacturer, context={"request": request}).data
        )
