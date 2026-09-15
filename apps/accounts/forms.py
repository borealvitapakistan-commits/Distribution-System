from django.contrib.auth.forms import (
    UserChangeForm as DjangoUserChangeForm,
)
from django.contrib.auth.forms import (
    UserCreationForm as DjangoUserCreationForm,
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
            "role",
            "is_active",
        )


class UserChangeForm(DjangoUserChangeForm):
    class Meta:
        model = User
        fields = "__all__"
