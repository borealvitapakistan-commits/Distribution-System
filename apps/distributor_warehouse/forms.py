from decimal import Decimal

from django import forms

from .models import DistributorInventory, DistributorLocation


class DistributorInventoryForm(forms.ModelForm):
    class Meta:
        model = DistributorInventory
        fields = ["code", "name", "active"]


class AllocateDistributorBatchForm(forms.Form):
    """One quantity field per possible destination — pick a region (from
    the global pool) or a warehouse (from a region's pool), and split
    across as many as wanted in one submission. Mirrors the Owner's
    AllocateBatchForm exactly, scoped to the Distributor's own
    warehouses."""

    DEST_PREFIX = "dest_qty_"

    def __init__(self, *args, destinations=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.destinations = list(destinations or [])

        for destination, label in self.destinations:
            self.fields[f"{self.DEST_PREFIX}{destination.pk}"] = forms.DecimalField(
                max_digits=18,
                decimal_places=4,
                min_value=Decimal("0"),
                required=False,
                initial=Decimal("0"),
                label=label,
            )

    def get_allocations(self):
        allocations = []

        for destination, _label in self.destinations:
            raw = self.cleaned_data.get(f"{self.DEST_PREFIX}{destination.pk}")
            quantity = raw or Decimal("0")

            if quantity > 0:
                allocations.append((destination, quantity))

        return allocations


class DistributorLocationForm(forms.Form):
    code = forms.CharField(max_length=50)
    name = forms.CharField(max_length=200)

    distributor_inventory = forms.ModelChoiceField(
        label="Inventory (region)",
        queryset=DistributorInventory.objects.none(),
        required=True,
        help_text="Required for a warehouse.",
    )

    address = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    area_square_feet = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=0,
        initial=0,
    )

    capacity_units = forms.DecimalField(
        max_digits=18,
        decimal_places=4,
        min_value=0,
        initial=0,
    )
    active = forms.BooleanField(required=False, initial=True)

    def __init__(
        self,
        *args,
        distributor_profile=None,
        instance=None,
        initial_inventory=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.instance = instance

        if distributor_profile is not None:
            self.fields["distributor_inventory"].queryset = (
                DistributorInventory.objects.filter(
                    distributor_profile=distributor_profile,
                    active=True,
                )
            )

        if instance is not None and not args:
            self.initial.update(
                {
                    "code": instance.code,
                    "name": instance.name,
                    "distributor_inventory": instance.distributor_inventory_id,
                    "address": instance.address,
                    "area_square_feet": instance.area_square_feet,
                    "capacity_units": instance.capacity_units,
                    "active": instance.active,
                }
            )
        elif initial_inventory is not None and not args:
            self.initial["distributor_inventory"] = initial_inventory.pk

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data["location_type"] = DistributorLocation.LocationType.WAREHOUSE
        return cleaned_data
