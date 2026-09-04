from rest_framework import serializers

from .models import AuditEvent


class AuditEventSerializer(serializers.ModelSerializer):
    actor = serializers.StringRelatedField()

    class Meta:
        model = AuditEvent

        fields = [
            "id",
            "actor",
            "action",
            "object_type",
            "object_id",
            "object_label",
            "before_data",
            "after_data",
            "reason",
            "request_id",
            "ip_address",
            "created_at",
        ]

        read_only_fields = fields