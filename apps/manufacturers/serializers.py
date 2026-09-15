from rest_framework import serializers

from .models import Manufacturer


class ManufacturerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Manufacturer
        fields = ["id", "name", "phone", "email", "address", "notes", "active"]
        read_only_fields = ["id"]

    def create(self, validated_data):
        from .services import create_manufacturer

        return create_manufacturer(
            actor=self.context["request"].user,
            **validated_data,
        )

    def update(self, instance, validated_data):
        from .services import update_manufacturer

        return update_manufacturer(
            actor=self.context["request"].user,
            manufacturer_id=instance.pk,
            **validated_data,
        )
