from decimal import Decimal

from django import forms

from apps.products.models import Product
from apps.owner_warehouse.models import Location


class WarehouseChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.name} · {obj.inventory.name}"


class ReceiveStockForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(active=True),
    )
    quantity = forms.DecimalField(
        max_digits=18,
        decimal_places=4,
        min_value=Decimal("0.0001"),
    )
    to_location = WarehouseChoiceField(
        label="Warehouse",
        queryset=Location.objects.filter(
            location_type=Location.LocationType.OWN,
            active=True,
            inventory__active=True,
        ).select_related("inventory"),
        empty_label="Choose a warehouse",
    )
    batch_number = forms.CharField(
        label="Batch number",
        max_length=100,
        help_text=(
            "This lot's own label, e.g. BV-GL-AML-001. Every delivery is "
            "its own batch — never merged with earlier stock."
        ),
    )
    received_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Defaults to today if left blank.",
    )
    expiry_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Soonest-to-expire batches are suggested first.",
    )
    reference = forms.CharField(
        label="Note",
        max_length=255,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
