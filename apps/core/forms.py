from django import forms
from .models import Brand


class BrandForm(forms.ModelForm):
    class Meta:
        model = Brand

        fields = [
            "name",
            "legal_name",
            "enlistment_number",
            "ntn",
            "strn",
            "base_currency",
            "timezone",
            "fiscal_year_start_month",
            "default_country",
            "default_low_stock_threshold",
            "logo",
            "address",
            "phone",
            "email",
        ]


        widgets = {
            "address": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": (
                        "Enter the brand address"
                    ),
                }
            ),
            "name": forms.TextInput(
                attrs={
                    "placeholder": "Brand name"
                }
            ),
            "legal_name": forms.TextInput(
                attrs={
                    "placeholder": "Registered legal name"
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "placeholder": "brand@example.com"
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "placeholder": "+92..."
                }
            ),
        }
