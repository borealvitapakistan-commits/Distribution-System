from django import forms

from apps.customers.models import Customer
from apps.distributors.models import DistributorProfile
from apps.manufacturers.models import Manufacturer

from .models import Location


class LocationForm(forms.Form):
    code = forms.CharField(max_length=50)
    name = forms.CharField(max_length=200)

    location_type = forms.ChoiceField(choices=Location.LocationType.choices)
    manufacturer = forms.ModelChoiceField(queryset=Manufacturer.objects.none(), required=False)
    distributor_profile = forms.ModelChoiceField(queryset=DistributorProfile.objects.none(), required=False)
    customer = forms.ModelChoiceField(queryset=Customer.objects.none(), required=False)
    is_sellable = forms.BooleanField(required=False)
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
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.instance = instance

        self.fields["manufacturer"].queryset = (
            Manufacturer.objects.filter(active=True)
        )
        self.fields["distributor_profile"].queryset = (
            DistributorProfile.objects.all()
        )
        self.fields["customer"].queryset = (
            Customer.objects.filter(active=True)
        )

        if instance is not None and not args:
            self.initial.update(
                {
                    "code": instance.code,
                    "name": instance.name,
                    "location_type": (
                        instance.location_type
                    ),
                    "manufacturer": instance.manufacturer_id,
                    "distributor_profile": instance.distributor_profile_id,
                    "customer": instance.customer_id,
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
