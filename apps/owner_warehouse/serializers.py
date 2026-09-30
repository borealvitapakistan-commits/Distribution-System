from rest_framework import serializers

from .models import Location
from .services import location_utilization


class LocationSerializer(serializers.ModelSerializer):
    location_type_display = serializers.CharField(source="get_location_type_display", read_only=True)
    inventory_name = serializers.CharField(source="inventory.name", read_only=True, allow_null=True)
    manufacturer_name = serializers.CharField(source="manufacturer.name", read_only=True, allow_null=True)
    utilization = serializers.SerializerMethodField()

    class Meta:
        model = Location
        fields = [
            "id",
            "code",
            "name",
            "location_type",
            "location_type_display",
            "inventory",
            "inventory_name",
            "manufacturer",
            "manufacturer_name",
            "on_book",
            "is_physical",
            "is_sellable",
            "address",
            "area_square_feet",
            "capacity_units",
            "active",
            "utilization",
        ]
        read_only_fields = [
            "id",
            "location_type_display",
            "inventory_name",
            "manufacturer_name",
            "on_book",
            "is_physical",
            "utilization",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        from apps.manufacturers.models import Manufacturer

        from .models import Inventory

        self.fields["inventory"].queryset = (Inventory.objects.filter(active=True))
        self.fields["manufacturer"].queryset = (Manufacturer.objects.filter(active=True))

    def get_utilization(self, obj):
        data = location_utilization(obj)
        data["quantity_on_hand"] = str(data["quantity_on_hand"])
        data["capacity_units"] = str(data["capacity_units"])

        if data["utilization_percentage"] is not None:
            data["utilization_percentage"] = str(data["utilization_percentage"])

        return data
