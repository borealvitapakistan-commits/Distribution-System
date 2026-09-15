from django.core.exceptions import PermissionDenied as DjangoPermissionDenied, ValidationError as DjangoValidationError
from django.db import IntegrityError
from rest_framework import status
from rest_framework.exceptions import (
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsDistributor, IsOwner

from .models import StockRequest
from .serializers import StockRequestSerializer
from .services import create_stock_request, decline_request, fulfill_request_item



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



class DistributorStockRequestListCreateAPIView(APIView):
    permission_classes = [IsDistributor]

    def get(self, request):
        requests = (
            StockRequest.objects
            .for_user(request.user)
            .prefetch_related("items__product")
        )

        return Response(
            StockRequestSerializer(requests, many=True).data
        )

    def post(self, request):
        from apps.products.models import Product

        items = []

        for row in request.data.get("items", []):
            product = Product.objects.filter(pk=row.get("product")).first()
            items.append(
                {
                    "product": product,
                    "quantity_requested": row.get("quantity_requested"),
                }
            )

        try:
            stock_request = create_stock_request(
                actor=request.user,
                items=items,
            )
        except (
            DjangoPermissionDenied,
            DjangoValidationError,
            IntegrityError,
        ) as exc:
            raise_api_error(exc)

        return Response(
            StockRequestSerializer(stock_request).data,
            status=status.HTTP_201_CREATED,
        )


class OwnerStockRequestListAPIView(APIView):
    permission_classes = [IsOwner]

    def get(self, request):
        requests = (
            StockRequest.objects
            .for_user(request.user)
            .select_related("distributor_profile")
            .prefetch_related("items__product")
        )

        return Response(
            StockRequestSerializer(requests, many=True).data
        )


class OwnerStockRequestDetailAPIView(APIView):
    permission_classes = [IsOwner]

    def get_object(self, request, request_id):
        stock_request = (
            StockRequest.objects
            .for_user(request.user)
            .filter(pk=request_id)
            .first()
        )

        if stock_request is None:
            raise NotFound("Request was not found in your permitted scope.")

        return stock_request

    def get(self, request, request_id):
        return Response(
            StockRequestSerializer(
                self.get_object(request, request_id)
            ).data
        )


class OwnerStockRequestDeclineAPIView(APIView):
    permission_classes = [IsOwner]

    def post(self, request, request_id):
        try:
            stock_request = decline_request(
                actor=request.user,
                request_id=request_id,
                comment=request.data.get("comment", ""),
            )
        except (
            DjangoPermissionDenied,
            DjangoValidationError,
            IntegrityError,
        ) as exc:
            raise_api_error(exc)

        return Response(StockRequestSerializer(stock_request).data)


class OwnerStockRequestItemFulfillAPIView(APIView):
    permission_classes = [IsOwner]

    def post(self, request, item_id):
        from apps.warehouse.models import Location

        from_location = Location.objects.filter(
            pk=request.data.get("from_location")
        ).first()

        try:
            item = fulfill_request_item(
                actor=request.user,
                item_id=item_id,
                quantity=request.data.get("quantity"),
                from_location=from_location,
            )
        except (
            DjangoPermissionDenied,
            DjangoValidationError,
            IntegrityError,
        ) as exc:
            raise_api_error(exc)

        return Response(
            StockRequestSerializer(item.request).data
        )
