from django import forms
from django.contrib.auth.password_validation import validate_password

from apps.accounts.models import User


class OwnerCreationForm(forms.Form):
    name = forms.CharField(
        label="Owner name",
        max_length=200,
        widget=forms.TextInput(attrs={"placeholder": "Business or contact name"}),
    )

    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"placeholder": "owner@example.com"})
    )

    first_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "First name"}),
    )

    last_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Last name"}),
    )

    phone = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "+92..."}),
    )

    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    temporary_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "Temporary password"})
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()

        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")

        return email

    def clean_temporary_password(self):
        password = self.cleaned_data["temporary_password"]
        validate_password(password)
        return password


class OwnerProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "phone"]
        widgets = {
            "first_name": forms.TextInput(attrs={"placeholder": "First name"}),
            "last_name": forms.TextInput(attrs={"placeholder": "Last name"}),
            "phone": forms.TextInput(attrs={"placeholder": "+92..."}),
        }
