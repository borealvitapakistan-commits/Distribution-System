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

from .models import (
    Ingredient,
    Product,
    ProductCategory,
)
from .serializers import (
    DistributorProductSerializer,
    IngredientSerializer,
    ProductCategorySerializer,
    ProductSerializer,
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



class CategoryListCreateAPIView(APIView):
    permission_classes = [IsOwner]

    def get(self, request):
        categories = (
            ProductCategory.objects
            .for_user(request.user)
            .select_related("parent")
        )

        return Response(
            ProductCategorySerializer(
                categories,
                many=True,
            ).data
        )

    def post(self, request):
        serializer = ProductCategorySerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        try:
            category = serializer.save()
        except (
            DjangoPermissionDenied,
            DjangoValidationError,
            IntegrityError,
        ) as exc:
            raise_api_error(exc)

        return Response(
            ProductCategorySerializer(category).data,
            status=status.HTTP_201_CREATED,
        )


class CategoryDetailAPIView(APIView):
    permission_classes = [IsOwner]

    def get_object(self, request, category_id):
        category = (
            ProductCategory.objects
            .for_user(request.user)
            .filter(pk=category_id)
            .first()
        )

        if category is None:
            raise NotFound(
                "Category was not found in your permitted scope."
            )

        return category

    def get(self, request, category_id):
        category = self.get_object(
            request,
            category_id,
        )

        return Response(
            ProductCategorySerializer(category).data
        )

    def patch(self, request, category_id):
        category = self.get_object(
            request,
            category_id,
        )

        serializer = ProductCategorySerializer(
            category,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        try:
            category = serializer.save()
        except (
            DjangoPermissionDenied,
            DjangoValidationError,
            IntegrityError,
        ) as exc:
            raise_api_error(exc)

        return Response(
            ProductCategorySerializer(category).data
        )


class IngredientListCreateAPIView(APIView):
    permission_classes = [IsOwner]

    def get(self, request):
        ingredients = Ingredient.objects.for_user(
            request.user
        )

        return Response(
            IngredientSerializer(
                ingredients,
                many=True,
            ).data
        )

    def post(self, request):
        serializer = IngredientSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        try:
            ingredient = serializer.save()
        except (
            DjangoPermissionDenied,
            DjangoValidationError,
            IntegrityError,
        ) as exc:
            raise_api_error(exc)

        return Response(
            IngredientSerializer(ingredient).data,
            status=status.HTTP_201_CREATED,
        )


class ProductListCreateAPIView(APIView):
    permission_classes = [IsOwner]

    def get(self, request):
        products = (
            Product.objects
            .for_user(request.user)
            .select_related("category")
            .prefetch_related(
                "ingredients__ingredient"
            )
        )

        return Response(
            ProductSerializer(
                products,
                many=True,
                context={"request": request},
            ).data
        )

    def post(self, request):
        serializer = ProductSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        try:
            product = serializer.save()
        except (
            DjangoPermissionDenied,
            DjangoValidationError,
            IntegrityError,
        ) as exc:
            raise_api_error(exc)

        return Response(
            ProductSerializer(
                product,
                context={"request": request},
            ).data,
            status=status.HTTP_201_CREATED,
        )


class ProductDetailAPIView(APIView):
    permission_classes = [IsOwner]

    def get_object(self, request, product_id):
        product = (
            Product.objects
            .for_user(request.user)
            .select_related("category")
            .prefetch_related(
                "ingredients__ingredient"
            )
            .filter(pk=product_id)
            .first()
        )

        if product is None:
            raise NotFound(
                "Product was not found in your permitted scope."
            )

        return product

    def get(self, request, product_id):
        return Response(
            ProductSerializer(
                self.get_object(request, product_id),
                context={"request": request},
            ).data
        )

    def patch(self, request, product_id):
        product = self.get_object(
            request,
            product_id,
        )

        serializer = ProductSerializer(
            product,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        try:
            product = serializer.save()
        except (
            DjangoPermissionDenied,
            DjangoValidationError,
            IntegrityError,
        ) as exc:
            raise_api_error(exc)

        return Response(
            ProductSerializer(
                product,
                context={"request": request},
            ).data
        )


class DistributorProductListAPIView(APIView):
    permission_classes = [IsDistributor]

    def get(self, request):
        products = (
            Product.objects
            .for_user(request.user)
            .select_related("category")
            .order_by("name", "sku")
        )

        return Response(
            DistributorProductSerializer(
                products,
                many=True,
            ).data
        )