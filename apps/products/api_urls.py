from django.urls import path

from .api import (
    CategoryDetailAPIView,
    CategoryListCreateAPIView,
    DistributorProductListAPIView,
    IngredientListCreateAPIView,
    ProductDetailAPIView,
    ProductListCreateAPIView,
)


urlpatterns = [
    path(
        "categories/",
        CategoryListCreateAPIView.as_view(),
        name="api-category-list",
    ),
    path(
        "categories/<uuid:category_id>/",
        CategoryDetailAPIView.as_view(),
        name="api-category-detail",
    ),
    path(
        "ingredients/",
        IngredientListCreateAPIView.as_view(),
        name="api-ingredient-list",
    ),
    path(
        "products/",
        ProductListCreateAPIView.as_view(),
        name="api-product-list",
    ),
    path(
        "products/<uuid:product_id>/",
        ProductDetailAPIView.as_view(),
        name="api-product-detail",
    ),
    path(
        "distributor/products/",
        DistributorProductListAPIView.as_view(),
        name="api-distributor-product-list",
    ),
]
