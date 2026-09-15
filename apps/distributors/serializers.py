from rest_framework import serializers

from .models import DistributorProfile


class DistributorProfileSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.pk", read_only=True, allow_null=True)
    user_email = serializers.EmailField(source="user.email", read_only=True, allow_null=True)
    approved_by = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = DistributorProfile
        fields = [
            "id",
            "name",
            "phone",
            "email",
            "address",
            "territory",
            "commission_percentage",
            "user_id",
            "user_email",
            "approval_status",
            "approved_by",
            "approved_at",
            "notes",
        ]
        read_only_fields = fields


class DistributorProfileUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False)
    phone = serializers.CharField(max_length=30, required=False)
    address = serializers.CharField(required=False, allow_blank=True)
    territory = serializers.CharField(max_length=150, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
