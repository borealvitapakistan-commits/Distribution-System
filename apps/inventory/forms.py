from decimal import Decimal

from django import forms

from apps.products.models import Product
from apps.distributors.models import DistributorProfile
from apps.warehouse.models import Location


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
    reference = forms.CharField(
        label="Note",
        max_length=255,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )


class GiveToDistributorForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(active=True),
    )
    quantity = forms.DecimalField(
        max_digits=18,
        decimal_places=4,
        min_value=Decimal("0.0001"),
    )
    from_location = forms.ModelChoiceField(
        label="Ship from warehouse",
        queryset=Location.objects.filter(
            location_type=Location.LocationType.OWN,
            active=True,
        ),
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
