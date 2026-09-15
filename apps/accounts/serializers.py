from django.contrib.auth import authenticate

from rest_framework import serializers

from .models import User


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()

    password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={
            "input_type": "password",
        },
    )

    def validate(self, attrs):
        request = self.context.get(
            "request"
        )

        user = authenticate(
            request=request,
            email=attrs["email"],
            password=attrs["password"],
        )

        if user is None:
            raise serializers.ValidationError(
                "Invalid email or password."
            )

        if not user.is_active:
            raise serializers.ValidationError(
                "This account is inactive."
            )

        if user.role not in {
            User.Role.OWNER,
            User.Role.DISTRIBUTOR,
        }:
            raise serializers.ValidationError(
                "The user role is invalid."
            )

        attrs["user"] = user

        return attrs


class UserSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(
        source="get_role_display",
        read_only=True,
    )

    class Meta:
        model = User

        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "phone",
            "role",
            "role_display",
            "is_active",
            "must_change_password",
            "last_password_changed_at",
        ]

        read_only_fields = [
            "id",
            "email",
            "role",
            "role_display",
            "is_active",
            "must_change_password",
            "last_password_changed_at",
        ]


class PasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()