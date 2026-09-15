from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory

from apps.products.models import Product
from apps.warehouse.models import Location

from .models import StockRequest, StockRequestItem


class StockRequestItemForm(forms.ModelForm):
    class Meta:
        model = StockRequestItem
        fields = ["product", "quantity_requested"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["product"].queryset = Product.objects.filter(active=True)


StockRequestItemFormSet = inlineformset_factory(
    StockRequest,
    StockRequestItem,
    form=StockRequestItemForm,
    extra=3,
    can_delete=True,
)


class FulfillRequestItemForm(forms.Form):
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


class DeclineRequestForm(forms.Form):
    comment = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class OwnerCommentForm(forms.Form):
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
