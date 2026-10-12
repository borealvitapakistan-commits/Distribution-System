from decimal import Decimal

from django import forms

from .models import Inventory, Location


class InventoryForm(forms.ModelForm):
    class Meta:
        model = Inventory
        fields = ["code", "name", "active"]


class AllocateBatchForm(forms.Form):
    """One quantity field per possible destination — pick a region
    (from the global pool) or a warehouse (from a region's pool), and
    split across as many as you want in one submission, same pattern as
    shipping a Purchase Order."""

    DEST_PREFIX = "dest_qty_"

    def __init__(self, *args, destinations=None, **kwargs):
        """destinations: [(location, label), ...] — label lets stage 1
        show the region's name instead of its holding-pool location name."""
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


class LocationForm(forms.Form):
    """A warehouse. There is only one kind the Owner creates — an owned
    warehouse inside an Inventory (region). Shopify and Distributors draw
    from these same warehouses, so there's no type to pick."""

    MANUAL_LOCATION_TYPES = [
        (Location.LocationType.OWN, Location.LocationType.OWN.label),
    ]
    MANUAL_LOCATION_TYPES_SET = {
        choice for choice, _ in MANUAL_LOCATION_TYPES
    }

    code = forms.CharField(max_length=50)
    name = forms.CharField(max_length=200)

    inventory = forms.ModelChoiceField(
        label="Inventory (region)",
        queryset=Inventory.objects.filter(active=True),
        help_text="Which region this warehouse belongs to.",
    )
    is_sellable = forms.BooleanField(
        label="Sellable",
        required=False,
        initial=True,
        help_text=(
            "Untick to hold this warehouse's stock back: nothing can be "
            "shipped to a Distributor or sold on Shopify from it until it's "
            "ticked again."
        ),
    )
    address = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={"rows": 3}
        ),
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
        instance=None,
        initial_inventory=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.instance = instance

        if instance is not None and not args:
            self.initial.update(
                {
                    "code": instance.code,
                    "name": instance.name,
                    "inventory": instance.inventory_id,
                    "is_sellable": instance.is_sellable,
                    "address": instance.address,
                    "area_square_feet": (
                        instance.area_square_feet
                    ),
                    "capacity_units": (
                        instance.capacity_units
                    ),
                    "active": instance.active,
                }
            )
        elif initial_inventory is not None and not args:
            self.initial["inventory"] = initial_inventory.pk

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data["location_type"] = Location.LocationType.OWN
        return cleaned_data
