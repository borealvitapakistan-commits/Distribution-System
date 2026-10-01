from rest_framework import serializers

from .models import PurchaseOrder, PurchaseOrderItem, PurchaseOrderPayment


class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    quantity_to_ship_remaining = serializers.DecimalField(
        max_digits=18, decimal_places=4, read_only=True
    )
    quantity_to_receive_remaining = serializers.DecimalField(
        max_digits=18, decimal_places=4, read_only=True
    )
    line_total = serializers.DecimalField(
        max_digits=18, decimal_places=2, read_only=True
    )

    class Meta:
        model = PurchaseOrderItem
        fields = [
            "id",
            "product",
            "product_name",
            "product_sku",
            "quantity_requested",
            "list_price",
            "discount_percentage",
            "unit_price",
            "unavailable",
            "owner_note",
            "quantity_shipped",
            "quantity_received",
            "quantity_to_ship_remaining",
            "quantity_to_receive_remaining",
            "line_total",
        ]
        read_only_fields = fields


class PurchaseOrderPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseOrderPayment
        fields = ["id", "amount", "paid_at", "proof", "note"]
        read_only_fields = fields


class PurchaseOrderSerializer(serializers.ModelSerializer):
    distributor_name = serializers.CharField(
        source="distributor_profile.name", read_only=True
    )
    items = PurchaseOrderItemSerializer(many=True, read_only=True)
    payments = PurchaseOrderPaymentSerializer(many=True, read_only=True)
    subtotal = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    tax_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    grand_total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total_paid = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    is_fully_paid = serializers.BooleanField(read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id",
            "po_number",
            "distributor_profile",
            "distributor_name",
            "status",
            "owner_comment",
            "tax_percentage",
            "shipping_amount",
            "invoiced_at",
            "created_at",
            "subtotal",
            "tax_amount",
            "grand_total",
            "total_paid",
            "is_fully_paid",
            "items",
            "payments",
        ]
        read_only_fields = fields
