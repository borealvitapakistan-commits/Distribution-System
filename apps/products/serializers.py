from rest_framework import serializers

from .models import (
    Ingredient,
    Product,
    ProductCategory,
    ProductIngredient,
)


class ProductCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductCategory
        fields = [
            "id",
            "name",
            "parent",
            "sort_order",
            "active",
        ]
        read_only_fields = ["id"]

    def create(self, validated_data):
        from .services import create_category

        return create_category(
            actor=self.context["request"].user,
            **validated_data,
        )

    def update(self, instance, validated_data):
        from .services import update_category

        return update_category(
            actor=self.context["request"].user,
            category_id=instance.pk,
            **validated_data,
        )


class IngredientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ingredient
        fields = [
            "id",
            "name",
            "botanical_name",
            "part_used",
            "default_unit",
            "active",
        ]
        read_only_fields = ["id"]

    def create(self, validated_data):
        from .services import create_ingredient

        return create_ingredient(
            actor=self.context["request"].user,
            **validated_data,
        )


class ProductIngredientSerializer(serializers.ModelSerializer):
    ingredient_name = serializers.CharField(
        source="ingredient.name",
        read_only=True,
    )

    class Meta:
        model = ProductIngredient
        fields = [
            "id",
            "ingredient",
            "ingredient_name",
            "strength",
            "unit",
            "is_medicinal",
            "extract_ratio",
            "equivalent_to",
            "sort_order",
        ]
        read_only_fields = [
            "id",
            "ingredient_name",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["ingredient"].queryset = (
            Ingredient.objects.filter(active=True)
        )


class ProductSerializer(serializers.ModelSerializer):
    ingredients = ProductIngredientSerializer(
        many=True,
        required=False,
    )
    category_name = serializers.CharField(
        source="category.name",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = Product
        fields = [
            "id",
            "category",
            "category_name",
            "manufacturer",
            "brand",
            "sku",
            "barcode",
            "handle",
            "name",
            "generic_name",
            "brand_name",
            "indications",
            "body_html",
            "tags",
            "enlistment_number",
            "form",
            "pack_size",
            "unit_of_measure",
            "units_per_case",
            "serving_size",
            "strength_basis",
            "base_retail_price",
            "compare_at_price",
            "weight_grams",
            "image",
            "currency",
            "shelf_life_months",
            "hs_code",
            "default_reorder_point",
            "default_reorder_quantity",
            "allow_fractional_quantity",
            "active",
            "ingredients",
        ]
        read_only_fields = [
            "id",
            "category_name",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["category"].queryset = (
            ProductCategory.objects.filter(active=True)
        )

        from apps.manufacturers.models import Manufacturer

        self.fields["manufacturer"].queryset = (
            Manufacturer.objects.filter(active=True)
        )

        from apps.core.models import Brand

        self.fields["brand"].queryset = (
            Brand.objects.filter(active=True)
        )

    def create(self, validated_data):
        from .services import create_product

        ingredients = validated_data.pop(
            "ingredients",
            [],
        )

        return create_product(
            actor=self.context["request"].user,
            ingredients=ingredients,
            **validated_data,
        )

    def update(self, instance, validated_data):
        from .services import update_product

        ingredients = validated_data.pop(
            "ingredients",
            None,
        )

        return update_product(
            actor=self.context["request"].user,
            product_id=instance.pk,
            ingredients=ingredients,
            **validated_data,
        )


class DistributorProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(
        source="category.name",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = Product
        fields = [
            "id",
            "sku",
            "barcode",
            "name",
            "generic_name",
            "brand_name",
            "indications",
            "form",
            "pack_size",
            "unit_of_measure",
            "units_per_case",
            "serving_size",
            "strength_basis",
            "base_retail_price",
            "currency",
            "category_name",
        ]
        read_only_fields = fields