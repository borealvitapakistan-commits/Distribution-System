from decimal import Decimal

from django import forms
from django.forms import BaseFormSet, formset_factory
from django.db.models import Q
from django.utils.text import slugify

from .models import Inventory, Location


def suggested_code(model, name, prefix, instance=None):
    """Let owners use names; generate a readable reference when left blank."""
    stem = f"{prefix}-{slugify(name).upper() or prefix}"[:42]
    existing = model.objects.all()
    if instance is not None:
        existing = existing.exclude(pk=instance.pk)
    code, index = stem, 2
    while existing.filter(code=code).exists():
        code = f"{stem}-{index}"
        index += 1
    return code


class InventoryForm(forms.ModelForm):
    code = forms.CharField(
        max_length=50, required=False, label="Reference code",
        help_text="Leave blank and we will create one for you.",
    )

    class Meta:
        model = Inventory
        fields = ["name", "code", "active"]
        labels = {"name": "Region name", "active": "Region is active"}
        widgets = {"name": forms.TextInput(attrs={"placeholder": "e.g. Lahore", "autofocus": True})}

    def clean_code(self):
        return self.cleaned_data.get("code", "").strip().upper() or suggested_code(
            Inventory, self.cleaned_data.get("name", ""), "REG", self.instance,
        )


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


class RegionChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.name


class LocationForm(forms.Form):
    """A warehouse. There is only one kind the Owner creates — an owned
    warehouse inside an Inventory (region). Shopify draws from these same
    warehouses, so there's no type to pick."""

    MANUAL_LOCATION_TYPES = [
        (Location.LocationType.OWN, Location.LocationType.OWN.label),
    ]
    MANUAL_LOCATION_TYPES_SET = {
        choice for choice, _ in MANUAL_LOCATION_TYPES
    }

    name = forms.CharField(max_length=200, label="Warehouse name", widget=forms.TextInput(
        attrs={"placeholder": "e.g. Johar Town warehouse", "autofocus": True},
    ))
    code = forms.CharField(max_length=50, required=False, label="Reference code",
                           help_text="Leave blank and we will create one for you.")

    inventory = RegionChoiceField(
        label="Region",
        queryset=Inventory.objects.filter(active=True),
        empty_label="Choose a region",
        help_text="The city or area where this warehouse belongs.",
    )
    is_sellable = forms.BooleanField(
        label="Stock here is available for sale",
        required=False,
        initial=True,
        help_text=(
            "Shopify can sell stock from this warehouse. Turn this off for stock you want to hold back."
        ),
    )
    address = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={"rows": 3}
        ),
    )

    area_square_feet = forms.DecimalField(
        label="Floor space (sq ft)", required=False,
        max_digits=12,
        decimal_places=2,
        min_value=0,
        initial=0,
    )

    capacity_units = forms.DecimalField(
        label="Storage capacity (units)", required=False,
        max_digits=18,
        decimal_places=4,
        min_value=0,
        initial=0,
    )
    active = forms.BooleanField(label="Warehouse is active", required=False, initial=True)

    def __init__(
        self,
        *args,
        instance=None,
        initial_inventory=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.instance = instance
        if instance is not None:
            self.fields["inventory"].queryset = Inventory.objects.filter(
                Q(active=True) | Q(pk=instance.inventory_id)
            )

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
        cleaned_data["area_square_feet"] = cleaned_data.get("area_square_feet") or Decimal("0")
        cleaned_data["capacity_units"] = cleaned_data.get("capacity_units") or Decimal("0")
        cleaned_data["code"] = cleaned_data.get("code", "").strip().upper() or suggested_code(
            Location, cleaned_data.get("name", ""), "WH", self.instance,
        )
        return cleaned_data


def placement_destinations(batch, region=None):
    """Only offer active destinations that this batch is allowed to use."""
    regions = Inventory.objects.filter(active=True)
    if batch.location.inventory_id:
        regions = regions.filter(pk=batch.location.inventory_id)
    elif region is not None:
        regions = regions.filter(pk=region.pk)
    warehouses = Location.objects.filter(
        active=True, location_type=Location.LocationType.OWN, inventory__in=regions,
    ).select_related("inventory")
    grouped, destinations = {}, {}
    for warehouse in warehouses:
        key = f"warehouse:{warehouse.pk}"
        label = f"{warehouse.name} · {warehouse.inventory.name}"
        if not warehouse.is_sellable:
            label += " (held from sale)"
        grouped.setdefault(warehouse.inventory_id, []).append((key, label))
        destinations[key] = warehouse
    choices = [("", "Choose a destination")]
    for region in regions:
        options = grouped.get(region.pk, [])
        if not batch.location.inventory_id:
            key = f"region:{region.pk}"
            options.append((key, f"{region.name} · choose warehouse later"))
            destinations[key] = region
        if options:
            choices.append((region.name, options))
    return choices, destinations


class PlacementRowForm(forms.Form):
    destination = forms.ChoiceField(label="Destination", choices=())
    quantity = forms.DecimalField(
        label="Quantity", max_digits=18, decimal_places=4, min_value=Decimal("0.0001"),
        widget=forms.NumberInput(attrs={"placeholder": "0", "inputmode": "decimal", "data-placement-quantity": ""}),
    )

    def __init__(self, *args, choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["destination"].choices = choices


class BasePlacementFormSet(BaseFormSet):
    def __init__(self, *args, batch, destinations, **kwargs):
        self.batch = batch
        self.destinations = destinations
        super().__init__(*args, **kwargs)

    def clean(self):
        if any(self.errors):
            return
        total, selected = Decimal("0"), set()
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            destination = form.cleaned_data.get("destination")
            if destination in selected:
                raise forms.ValidationError("Choose each destination once. Combine its quantities in one row.")
            selected.add(destination)
            total += form.cleaned_data.get("quantity", Decimal("0"))
        if total <= 0:
            raise forms.ValidationError("Choose a destination and enter the quantity you want to place.")
        if total > self.batch.quantity_remaining:
            raise forms.ValidationError(
                f"You entered {total.normalize():f} units, but this batch has only "
                f"{self.batch.quantity_remaining.normalize():f} units available. Reduce the quantities below."
            )

    def get_allocations(self):
        return [
            (self.destinations[form.cleaned_data["destination"]], form.cleaned_data["quantity"])
            for form in self.forms if form.cleaned_data and not form.cleaned_data.get("DELETE")
        ]


PlacementFormSet = formset_factory(
    PlacementRowForm, formset=BasePlacementFormSet, extra=0, min_num=1, validate_min=True,
    can_delete=True, max_num=25, validate_max=True, absolute_max=50,
)
