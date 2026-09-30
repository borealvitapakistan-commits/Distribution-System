from decimal import Decimal

from django import forms
from .models import Brand


class BrandForm(forms.ModelForm):
    class Meta:
        model = Brand

        fields = [
            "name",
            "legal_name",
            "base_currency",
            "timezone",
            "fiscal_year_start_month",
            "default_country",
            "logo",
            "address",
            "phone",
            "email",
            "active",
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


class PaidQuestionForm(forms.Form):
    """A yes/no question where "yes" needs the payment details (amount,
    date, proof) and "no" needs nothing more."""

    QUESTION = ""

    answer = forms.TypedChoiceField(
        choices=[("yes", "Yes"), ("no", "No")],
        coerce=lambda value: value == "yes",
        widget=forms.RadioSelect,
    )
    amount = forms.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
    )
    paid_at = forms.DateField(
        label="Date paid",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    proof = forms.FileField(
        label="Payment proof",
        required=False,
        help_text="Bank slip, screenshot, etc.",
    )
    note = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["answer"].label = self.QUESTION

    def clean(self):
        cleaned_data = super().clean()

        if cleaned_data.get("answer"):
            for name in ("amount", "paid_at", "proof"):
                if not cleaned_data.get(name):
                    self.add_error(name, "Required when you've paid.")

        return cleaned_data
