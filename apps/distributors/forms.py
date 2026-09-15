from decimal import Decimal

from django import forms
from django.contrib.auth.password_validation import validate_password

from apps.accounts.models import User


class DistributorInvitationForm(forms.Form):
    """The single "Owner adds a Distributor" screen.

    Collects the login details and the business details (including the
    commission percentage) together, since a Distributor exists as one
    standalone profile, not a separate Party record linked afterward.
    """

    name = forms.CharField(
        label="Distributor name",
        max_length=200,
        widget=forms.TextInput(
            attrs={"placeholder": "Business or contact name"}
        ),
    )

    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                "placeholder": (
                    "distributor@example.com"
                )
            }
        )
    )

    first_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "First name"
            }
        ),
    )

    last_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Last name"
            }
        ),
    )

    phone = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "+92..."
            }
        ),
    )

    territory = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={"placeholder": "Territory"}
        ),
    )

    address = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    commission_percentage = forms.DecimalField(
        label="Commission %",
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0"),
        max_value=Decimal("100"),
        initial=Decimal("0"),
    )

    temporary_password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "placeholder": (
                    "Temporary password"
                )
            }
        )
    )

    def clean_email(self):
        email = (
            self.cleaned_data["email"]
            .strip()
            .lower()
        )

        if User.objects.filter(
            email__iexact=email
        ).exists():
            raise forms.ValidationError(
                "A user with this email "
                "already exists."
            )

        return email

    def clean_temporary_password(self):
        password = self.cleaned_data[
            "temporary_password"
        ]

        validate_password(password)

        return password


class DistributorProfileForm(forms.ModelForm):
    class Meta:
        model = User

        fields = [
            "first_name",
            "last_name",
            "phone",
        ]

        widgets = {
            "first_name": forms.TextInput(
                attrs={
                    "placeholder": "First name"
                }
            ),
            "last_name": forms.TextInput(
                attrs={
                    "placeholder": "Last name"
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "placeholder": "+92..."
                }
            ),
        }
