from decimal import Decimal

from django import forms
from django.utils.html import format_html

from .models import Brand


class ColorPickerWidget(forms.TextInput):
    """A colour swatch you can click and drag to pick from, kept in sync
    with a hex-code box you can type or paste into."""

    def render(self, name, value, attrs=None, renderer=None):
        attrs = {
            **(attrs or {}),
            "data-color-hex": "",
            "maxlength": "7",
            "placeholder": "#1f7a4d",
        }
        text_input = super().render(name, value, attrs, renderer)
        return format_html(
            '<div class="color-picker" data-color-picker>'
            '<input type="color" value="{}" data-color-swatch aria-label="Pick a colour">'
            "{}</div>",
            value or "#1f7a4d",
            text_input,
        )


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
            "primary_color",
            "address",
            "phone",
            "email",
            "active",
        ]


        widgets = {
            "primary_color": ColorPickerWidget(),
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
