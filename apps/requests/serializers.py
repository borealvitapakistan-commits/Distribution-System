from rest_framework import serializers

from .models import StockRequest, StockRequestItem


class StockRequestItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    quantity_remaining = serializers.DecimalField(
        max_digits=18, decimal_places=4, read_only=True
    )

    class Meta:
        model = StockRequestItem
        fields = [
            "id",
            "product",
            "product_name",
            "product_sku",
            "quantity_requested",
            "quantity_fulfilled",
            "quantity_remaining",
        ]
        read_only_fields = fields


class StockRequestSerializer(serializers.ModelSerializer):
    distributor_name = serializers.CharField(
        source="distributor_profile.name", read_only=True
    )
    items = StockRequestItemSerializer(many=True, read_only=True)

    class Meta:
        model = StockRequest
        fields = [
            "id",
            "distributor_profile",
            "distributor_name",
            "status",
            "owner_comment",
            "created_at",
            "items",
        ]
        read_only_fields = fields
