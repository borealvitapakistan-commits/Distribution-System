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

from .models import PurchaseOrder
from .serializers import PurchaseOrderSerializer
from .services import (
    create_purchase_order,
    decline_purchase_order,
    receive_purchase_order_item,
    ship_purchase_order_item,
)



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



class DistributorPurchaseOrderListCreateAPIView(APIView):
    permission_classes = [IsDistributor]

    def get(self, request):
        purchase_orders = (
            PurchaseOrder.objects
            .for_user(request.user)
            .prefetch_related("items__product", "payments")
        )

        return Response(
            PurchaseOrderSerializer(purchase_orders, many=True).data
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
            purchase_order = create_purchase_order(
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
            PurchaseOrderSerializer(purchase_order).data,
            status=status.HTTP_201_CREATED,
        )


class DistributorPurchaseOrderItemReceiveAPIView(APIView):
    permission_classes = [IsDistributor]

    def post(self, request, item_id):
        try:
            item = receive_purchase_order_item(
                actor=request.user,
                item_id=item_id,
                quantity=request.data.get("quantity"),
            )
        except (
            DjangoPermissionDenied,
            DjangoValidationError,
            IntegrityError,
        ) as exc:
            raise_api_error(exc)

        return Response(
            PurchaseOrderSerializer(item.purchase_order).data
        )


class OwnerPurchaseOrderListAPIView(APIView):
    permission_classes = [IsOwner]

    def get(self, request):
        purchase_orders = (
            PurchaseOrder.objects
            .for_user(request.user)
            .select_related("distributor_profile")
            .prefetch_related("items__product", "payments")
        )

        return Response(
            PurchaseOrderSerializer(purchase_orders, many=True).data
        )


class OwnerPurchaseOrderDetailAPIView(APIView):
    permission_classes = [IsOwner]

    def get_object(self, request, purchase_order_id):
        purchase_order = (
            PurchaseOrder.objects
            .for_user(request.user)
            .filter(pk=purchase_order_id)
            .first()
        )

        if purchase_order is None:
            raise NotFound("Purchase order was not found in your permitted scope.")

        return purchase_order

    def get(self, request, purchase_order_id):
        return Response(
            PurchaseOrderSerializer(
                self.get_object(request, purchase_order_id)
            ).data
        )


class OwnerPurchaseOrderDeclineAPIView(APIView):
    permission_classes = [IsOwner]

    def post(self, request, purchase_order_id):
        try:
            purchase_order = decline_purchase_order(
                actor=request.user,
                purchase_order_id=purchase_order_id,
                comment=request.data.get("comment", ""),
            )
        except (
            DjangoPermissionDenied,
            DjangoValidationError,
            IntegrityError,
        ) as exc:
            raise_api_error(exc)

        return Response(PurchaseOrderSerializer(purchase_order).data)


class OwnerPurchaseOrderItemShipAPIView(APIView):
    permission_classes = [IsOwner]

    def post(self, request, item_id):
        from apps.owner_inventory.models import StockBatch

        allocations = []

        for row in request.data.get("allocations", []):
            batch = StockBatch.objects.filter(pk=row.get("batch")).first()
            if batch is not None:
                allocations.append((batch, row.get("quantity")))

        try:
            item = ship_purchase_order_item(
                actor=request.user,
                item_id=item_id,
                allocations=allocations,
            )
        except (
            DjangoPermissionDenied,
            DjangoValidationError,
            IntegrityError,
        ) as exc:
            raise_api_error(exc)

        return Response(
            PurchaseOrderSerializer(item.purchase_order).data
        )
