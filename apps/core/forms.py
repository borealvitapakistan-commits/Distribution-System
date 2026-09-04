from django import forms
from .models import Company


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company

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
                        "Enter the company address"
                    ),
                }
            ),
            "name": forms.TextInput(
                attrs={
                    "placeholder": "Company name"
                }
            ),
            "legal_name": forms.TextInput(
                attrs={
                    "placeholder": "Registered legal name"
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "placeholder": "company@example.com"
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "placeholder": "+92..."
                }
            ),
        }
