from decimal import Decimal

from django import forms

from apps.distributors.models import DistributorProfile
from apps.products.models import Product

from .models import Agreement


class AgreementForm(forms.ModelForm):
    class Meta:
        model = Agreement
        fields = [
            "distributor_profile",
            "discount_percentage",
            "start_date",
            "end_date",
            "terms",
        ]
        labels = {
            "distributor_profile": "Distributor",
            "discount_percentage": "Default discount %",
        }
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "terms": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["distributor_profile"].queryset = DistributorProfile.objects.filter(
            approval_status=DistributorProfile.ApprovalStatus.APPROVED
        )
        self.fields["discount_percentage"].widget.attrs.update(
            {"min": "0", "max": "100", "step": "0.01"}
        )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")

        if start and end and end < start:
            self.add_error("end_date", "The end date can't be before the start date.")

        return cleaned


class ProductRateForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(active=True),
        required=False,
    )
    discount_percentage = forms.DecimalField(
        label="Discount %",
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0"),
        max_value=Decimal("100"),
        required=False,
    )

    def clean(self):
        cleaned = super().clean()

        if cleaned.get("product") and cleaned.get("discount_percentage") is None:
            self.add_error("discount_percentage", "Enter this product's discount.")

        return cleaned


ProductRateFormSet = forms.formset_factory(ProductRateForm, extra=1)


def product_rate_rows(formset):
    return [
        (form.cleaned_data["product"], form.cleaned_data["discount_percentage"])
        for form in formset.forms
        if form.cleaned_data.get("product")
    ]


class SignAgreementForm(forms.Form):
    signature = forms.CharField(
        max_length=200,
        label="Type your full name to sign",
    )


class ReasonForm(forms.Form):
    reason = forms.CharField(
        max_length=255,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
