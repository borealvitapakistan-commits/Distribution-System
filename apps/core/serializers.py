from rest_framework import serializers
from .models import Company, FiscalPeriod


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company

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


class FiscalPeriodSerializer(serializers.ModelSerializer):
    closed_by = serializers.StringRelatedField()
    locked_by = serializers.StringRelatedField()

    class Meta:
        model = FiscalPeriod

        fields = [
            "id",
            "year",
            "month",
            "start_date",
            "end_date",
            "status",
            "closed_by",
            "closed_at",
            "locked_by",
            "locked_at",
            "notes",
        ]

        read_only_fields = [
            "id",
            "year",
            "month",
            "start_date",
            "end_date",
            "status",
            "closed_by",
            "closed_at",
            "locked_by",
            "locked_at",
            "notes",
        ]


class PeriodActionSerializer(serializers.Serializer):
    reason = serializers.CharField(
        max_length=1000,
        allow_blank=False,
        trim_whitespace=True,
    )