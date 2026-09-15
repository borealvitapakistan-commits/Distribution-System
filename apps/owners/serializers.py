from rest_framework import serializers

from .models import OwnerProfile


class OwnerProfileSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.pk", read_only=True, allow_null=True)
    user_email = serializers.EmailField(source="user.email", read_only=True, allow_null=True)

    class Meta:
        model = OwnerProfile
        fields = [
            "id",
            "name",
            "phone",
            "email",
            "notes",
            "active",
            "user_id",
            "user_email",
        ]
        read_only_fields = fields


class OwnerProfileUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False)
    phone = serializers.CharField(max_length=30, required=False)
