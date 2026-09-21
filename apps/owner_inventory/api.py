from django.core.exceptions import PermissionDenied as DjangoPermissionDenied, ValidationError as DjangoValidationError
from django.db import IntegrityError
from rest_framework import status
from rest_framework.exceptions import (
    PermissionDenied,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsOwner

from .models import StockBalance
from .serializers import StockBalanceSerializer
from .services import give_to_distributor, receive_stock



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



class StockBalanceListAPIView(APIView):
    permission_classes = [IsOwner]

    def get(self, request):
        balances = (
            StockBalance.objects
            .for_user(request.user)
            .select_related("product", "location")
            .filter(quantity__gt=0)
        )

        return Response(
            StockBalanceSerializer(balances, many=True).data
        )


class ReceiveStockAPIView(APIView):
    permission_classes = [IsOwner]

    def post(self, request):
        data = request.data

        try:
            from .models import StockMovement
            from apps.products.models import Product
            from apps.owner_warehouse.models import Location

            product = Product.objects.for_user(request.user).filter(
                pk=data.get("product")
            ).first()
            to_location = Location.objects.filter(
                pk=data.get("to_location")
            ).first()

            movement = receive_stock(
                actor=request.user,
                product=product,
                quantity=data.get("quantity"),
                to_location=to_location,
                reference=data.get("reference", ""),
                received_date=data.get("received_date"),
                expiry_date=data.get("expiry_date"),
            )
        except (
            DjangoPermissionDenied,
            DjangoValidationError,
            IntegrityError,
        ) as exc:
            raise_api_error(exc)

        return Response(
            {"movement_id": str(movement.pk)},
            status=status.HTTP_201_CREATED,
        )


class GiveToDistributorAPIView(APIView):
    permission_classes = [IsOwner]

    def post(self, request):
        data = request.data

        try:
            from apps.distributors.models import DistributorProfile

            from .models import StockBatch

            batch = StockBatch.objects.filter(pk=data.get("batch")).first()
            distributor_profile = DistributorProfile.objects.filter(
                pk=data.get("distributor")
            ).first()

            movement = give_to_distributor(
                actor=request.user,
                product=batch.product if batch else None,
                quantity=data.get("quantity"),
                from_location=batch.location if batch else None,
                distributor_profile=distributor_profile,
                batch=batch,
                reference=data.get("reference", ""),
            )
        except (
            DjangoPermissionDenied,
            DjangoValidationError,
            IntegrityError,
        ) as exc:
            raise_api_error(exc)

        return Response(
            {"movement_id": str(movement.pk)},
            status=status.HTTP_201_CREATED,
        )
