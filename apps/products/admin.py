from django.contrib import admin

from .models import (
    Ingredient,
    Product,
    ProductCategory,
    ProductIngredient,
)


class ProductIngredientInline(admin.TabularInline):
    model = ProductIngredient
    extra = 0


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "parent",
        "active",
    )
    list_filter = ("active",)
    search_fields = ("name",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "sku",
        "name",
        "unit_of_measure",
        "active",
    )
    list_filter = (
        "active",
        "unit_of_measure",
    )
    search_fields = (
        "sku",
        "barcode",
        "name",
        "generic_name",
    )
    inlines = [ProductIngredientInline]


@admin.register(Ingredient)
class IngredientAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "default_unit",
        "active",
    )
    list_filter = ("active",)
    search_fields = (
        "name",
        "botanical_name",
    )