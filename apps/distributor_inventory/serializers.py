from rest_framework import serializers

from .models import DistributorStockBalance


class DistributorStockBalanceSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = DistributorStockBalance
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
