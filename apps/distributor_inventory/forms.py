from decimal import Decimal

from django import forms

from apps.distributor_warehouse.models import DistributorLocation
from apps.products.models import Product

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
        help_text="Every delivery is its own batch — never merged with earlier stock.",
    )
    received_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Defaults to today if left blank.",
    )
    expiry_date = forms.DateField(
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


class SubDistributorSaleForm(forms.Form):
    """Pick the product, then take whatever you want from each batch of
    it — the same way the Owner ships stock: one quantity per batch, split
    across as many batches and warehouses as needed in one submission.
    Every batch drawn from becomes its own sale record, so each one keeps
    its own batch number and expiry."""

    BATCH_FIELD_PREFIX = "batch_qty_"

    sub_distributor_name = forms.CharField(
        label="Sub-distributor name",
        max_length=200,
    )
    product = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        help_text="Only products you have in a warehouse are listed.",
    )
    sale_date = forms.DateField(
        label="Date",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Defaults to today if left blank.",
    )
    note = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    payment_proof = forms.FileField(
        label="Payment proof",
        required=False,
        help_text=(
            "Receipt, online transfer screenshot, photo. Leave blank if they "
            "haven't paid yet — you can add it later from the sale."
        ),
    )

    def __init__(self, *args, distributor_profile=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.batches = []

        if distributor_profile is not None:
            self.batches = list(
                available_batches_fefo(distributor_profile=distributor_profile)
                .filter(location__location_type=DistributorLocation.LocationType.WAREHOUSE)
                .select_related("product", "location", "location__distributor_inventory")
            )
            self.fields["product"].queryset = Product.objects.filter(
                pk__in={batch.product_id for batch in self.batches}
            )

        for batch in self.batches:
            self.fields[f"{self.BATCH_FIELD_PREFIX}{batch.pk}"] = forms.DecimalField(
                max_digits=18,
                decimal_places=4,
                min_value=Decimal("0"),
                max_value=batch.quantity_remaining,
                required=False,
                initial=Decimal("0"),
                label=f"{batch.batch_number or 'No batch number'} — quantity to sell",
            )

    def main_fields(self):
        return [
            self[name]
            for name in ("sub_distributor_name", "product", "sale_date", "payment_proof", "note")
        ]

    def batch_rows(self):
        """[(batch, quantity_bound_field), ...] — soonest-to-expire first."""
        return [(batch, self[f"{self.BATCH_FIELD_PREFIX}{batch.pk}"]) for batch in self.batches]

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get("product")
        self.allocations = []

        if product is None:
            return cleaned_data

        # Only the chosen product's batches count — anything typed against
        # another product before switching is ignored.
        for batch in self.batches:
            quantity = cleaned_data.get(f"{self.BATCH_FIELD_PREFIX}{batch.pk}")

            if batch.product_id == product.pk and quantity:
                self.allocations.append((batch, quantity))

        if not self.allocations and not self.errors:
            raise forms.ValidationError(
                "Enter a quantity to sell from at least one batch."
            )

        return cleaned_data


class SalePaymentProofForm(forms.Form):
    proof = forms.FileField(
        label="Payment proof",
        help_text="Receipt, online transfer screenshot, photo.",
    )
