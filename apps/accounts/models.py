import uuid

from django.contrib.auth.models import (
    AbstractUser,
    BaseUserManager,
)
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class UserManager(BaseUserManager):
    """Manager for creating Owner and Distributor users."""

    use_in_migrations = True
    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError(
                "Email is required."
            )

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)

        return user

    def create_user(self, email, password=None, role=None, **extra_fields):
        valid_roles = {
            "OWNER",
            "DISTRIBUTOR",
        }

        if role not in valid_roles:
            raise ValueError(
                "Role must be OWNER or DISTRIBUTOR."
            )

        extra_fields["role"] = role
        extra_fields.setdefault(
            "is_staff",
            False,
        )
        extra_fields.setdefault(
            "is_superuser",
            False,
        )

        return self._create_user(
            email,
            password,
            **extra_fields,
        )

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields["role"] = "OWNER"
        extra_fields["is_staff"] = True
        extra_fields["is_superuser"] = True

        return self._create_user(
            email,
            password,
            **extra_fields,
        )


class User(AbstractUser):
    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        DISTRIBUTOR = "DISTRIBUTOR", "Distributor"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4,editable=False)
    username = None
    email = models.EmailField(unique=True, db_index=True,)
    role = models.CharField(max_length=20, choices=Role.choices)
    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["email"]

        constraints = [
            models.CheckConstraint(
                condition=Q(
                    role__in=[
                        "OWNER",
                        "DISTRIBUTOR",
                    ]
                ),
                name="user_has_valid_role",
            ),
            models.CheckConstraint(
                condition=(
                    Q(is_superuser=False)
                    | Q(role="OWNER")
                ),
                name="superuser_must_be_owner",
            ),
        ]

    def clean(self):
        super().clean()

        if self.is_superuser and self.role != self.Role.OWNER:
            raise ValidationError(
                {
                    "role": (
                        "A superuser must have "
                        "the Owner role."
                    )
                }
            )

    @property
    def is_owner(self):
        return self.role == self.Role.OWNER

    @property
    def is_distributor(self):
        return self.role == self.Role.DISTRIBUTOR

    def __str__(self):
        return self.email