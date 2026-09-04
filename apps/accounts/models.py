import uuid

from django.contrib.auth.models import (
    AbstractUser,
    BaseUserManager,
)
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


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

        if (not extra_fields.get("company") and not extra_fields.get("company_id")):
            raise ValueError(
                "A company is required for a business user."
            )

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

    def create_superuser(self, email, password=None, **extra_fields,):
        extra_fields.setdefault(
            "role",
            "OWNER",
        )
        extra_fields.setdefault(
            "is_staff",
            True,
        )
        extra_fields.setdefault(
            "is_superuser",
            True,
        )

        if extra_fields["role"] != "OWNER":
            raise ValueError(
                "A superuser must have the OWNER role."
            )

        if extra_fields["is_staff"] is not True:
            raise ValueError(
                "A superuser must have is_staff=True."
            )

        if extra_fields["is_superuser"] is not True:
            raise ValueError(
                "A superuser must have "
                "is_superuser=True."
            )

        return self._create_user(
            email,
            password,
            **extra_fields,
        )

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.lower()

        super().save(*args, **kwargs)


class User(AbstractUser):
    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        DISTRIBUTOR = "DISTRIBUTOR", "Distributor"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4,editable=False)
    username = None
    email = models.EmailField(unique=True, db_index=True,)
    role = models.CharField(max_length=20, choices=Role.choices)
    company = models.ForeignKey(
        "core.Company",
        on_delete=models.PROTECT,
        related_name="users",
        null=True,
        blank=True,
    )
    phone = models.CharField(max_length=30, blank=True)
    must_change_password = models.BooleanField(default=False)
    last_password_changed_at = models.DateTimeField(null=True, blank=True,)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["email"]

        permissions = [
            (
                "manage_owner_accounts",
                "Can manage Owner accounts",
            ),
            (
                "manage_distributor_accounts",
                "Can manage Distributor accounts",
            ),
            (
                "approve_distributor_accounts",
                "Can approve Distributor accounts",
            ),
        ]

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

        if self.email:
            self.email = self.email.lower()

        if (
            self.is_superuser
            and self.role != self.Role.OWNER
        ):
            raise ValidationError(
                {
                    "role": (
                        "A superuser must have "
                        "the Owner role."
                    )
                }
            )
        
    def set_password(self, raw_password):
        super().set_password(raw_password)

        if raw_password:
            self.last_password_changed_at = timezone.now()

    @property
    def is_owner(self):
        return self.role == self.Role.OWNER

    @property
    def is_distributor(self):
        return self.role == self.Role.DISTRIBUTOR

    def __str__(self):
        return self.email