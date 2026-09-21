from rest_framework import serializers

from .models import StockBalance, StockMovement


class StockBalanceSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = StockBalance
        fields = [
            "id",
            "product",
            "product_name",
            "product_sku",
            "location",
            "location_name",
            "quantity",
        ]
        read_only_fields = fields


class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "product",
            "product_name",
            "from_location",
            "to_location",
            "quantity",
            "movement_type",
            "reference",
            "created_at",
            "created_by",
        ]
        read_only_fields = fields
