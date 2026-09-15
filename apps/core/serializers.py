from rest_framework import serializers
from .models import Brand


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand

        fields = [
            "id",
            "name",
            "legal_name",
            "enlistment_number",
            "ntn",
            "strn",
            "base_currency",
            "timezone",
            "fiscal_year_start_month",
            "default_country",
            "default_low_stock_threshold",
            "logo",
            "address",
            "phone",
            "email",
            "active",
        ]

        read_only_fields = [
            "id",
            "active",
        ]
