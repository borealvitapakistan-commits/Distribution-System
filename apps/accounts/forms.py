from django import forms
from django.contrib.auth.forms import (
    UserChangeForm as DjangoUserChangeForm,
)
from django.contrib.auth.forms import (
    UserCreationForm as DjangoUserCreationForm,
)
from django.contrib.auth.password_validation import (
    validate_password,
)

from .models import User


class UserCreationForm(DjangoUserCreationForm):
    class Meta:
        model = User

        fields = (
            "email",
            "first_name",
            "last_name",
            "phone",
            "company",
            "role",
            "is_active",
        )


class UserChangeForm(DjangoUserChangeForm):
    class Meta:
        model = User
        fields = "__all__"



class DistributorInvitationForm(forms.Form):
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