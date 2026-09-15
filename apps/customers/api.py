from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError

from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsOwner

from .models import Customer
from .serializers import CustomerSerializer


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


def _get_scoped_customer(request, customer_id):
    customer = (
        Customer.objects.for_user(request.user)
        .filter(pk=customer_id)
        .first()
    )
    if customer is None:
        raise NotFound("Customer was not found in your permitted scope.")
    return customer


class CustomerListCreateAPIView(APIView):
    permission_classes = [IsOwner]

    def get(self, request):
        queryset = Customer.objects.for_user(request.user)
        return Response(
            CustomerSerializer(queryset, many=True, context={"request": request}).data
        )

    def post(self, request):
        serializer = CustomerSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        try:
            customer = serializer.save()
        except (DjangoPermissionDenied, DjangoValidationError, IntegrityError) as exc:
            _raise_api_error(exc)
        return Response(
            CustomerSerializer(customer, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class CustomerDetailAPIView(APIView):
    permission_classes = [IsOwner]

    def get(self, request, customer_id):
        customer = _get_scoped_customer(request, customer_id)
        return Response(
            CustomerSerializer(customer, context={"request": request}).data
        )

    def patch(self, request, customer_id):
        customer = _get_scoped_customer(request, customer_id)
        serializer = CustomerSerializer(
            customer,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        try:
            customer = serializer.save()
        except (DjangoPermissionDenied, DjangoValidationError, IntegrityError) as exc:
            _raise_api_error(exc)
        return Response(
            CustomerSerializer(customer, context={"request": request}).data
        )
