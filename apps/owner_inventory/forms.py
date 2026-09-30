from decimal import Decimal

from django import forms

from apps.distributors.models import DistributorProfile
from apps.products.models import Product
from apps.owner_warehouse.models import Location

from .models import StockBatch
from .services import available_batches_fefo


class ReceiveStockForm(forms.Form):
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
        queryset=Location.objects.filter(
            location_type=Location.LocationType.OWN,
            active=True,
        ),
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


class BatchChoiceField(forms.ModelChoiceField):
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


class GiveToDistributorForm(forms.Form):
    batch = BatchChoiceField(
        label="Product batch (oldest/soonest-to-expire suggested first)",
        queryset=StockBatch.objects.none(),
    )
    quantity = forms.DecimalField(
        max_digits=18,
        decimal_places=4,
        min_value=Decimal("0.0001"),
    )
    distributor = forms.ModelChoiceField(
        queryset=DistributorProfile.objects.filter(
            approval_status=DistributorProfile.ApprovalStatus.APPROVED,
        ),
    )
    reference = forms.CharField(
        label="Note",
        max_length=255,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["batch"].queryset = available_batches_fefo()
