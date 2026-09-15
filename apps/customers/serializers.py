from rest_framework import serializers

from .models import Customer


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ["id", "name", "phone", "email", "address", "notes", "active"]
        read_only_fields = ["id"]

    def create(self, validated_data):
        from .services import create_customer

        return create_customer(
            actor=self.context["request"].user,
            **validated_data,
        )

    def update(self, instance, validated_data):
        from .services import update_customer

        return update_customer(
            actor=self.context["request"].user,
            customer_id=instance.pk,
            **validated_data,
        )
