from decimal import Decimal

from django import forms

from apps.distributor_warehouse.models import DistributorLocation
from apps.products.models import Product

from .models import DistributorStockBatch
from .services import available_batches_fefo


class DistributorReceiveStockForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(active=True),
    )
    quantity = forms.DecimalField(
        max_digits=18,
        decimal_places=4,
        min_value=Decimal("0.0001"),
    )
    to_location = forms.ModelChoiceField(
        label="Warehouse",
        queryset=DistributorLocation.objects.none(),
        required=False,
        help_text="Leave blank to land in your unallocated pool.",
    )
    batch_number = forms.CharField(
        label="Batch number",
        max_length=100,
        required=False,
    )
    received_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Defaults to today if left blank.",
    )
    expiry_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    reference = forms.CharField(
        label="Note",
        max_length=255,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, distributor_profile=None, **kwargs):
        super().__init__(*args, **kwargs)

        if distributor_profile is not None:
            self.fields["to_location"].queryset = DistributorLocation.objects.filter(
                distributor_profile=distributor_profile,
                location_type=DistributorLocation.LocationType.WAREHOUSE,
                active=True,
            )


class DistributorBatchChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, batch):
        expiry = (
            f"expires {batch.expiry_date}"
            if batch.expiry_date
            else "no expiry set"
        )
        number = f"{batch.batch_number} | " if batch.batch_number else ""
        return (
            f"{number}{batch.product.sku} - {batch.product.name} | "
            f"{batch.location.name} | received {batch.received_date} | "
            f"{expiry} | {batch.quantity_remaining} remaining"
        )
