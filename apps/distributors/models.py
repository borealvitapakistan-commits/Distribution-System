from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import AuditedModel

from .querysets import DistributorProfileQuerySet


class DistributorProfile(AuditedModel):
    class ApprovalStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        SUSPENDED = "SUSPENDED", "Suspended"
        REJECTED = "REJECTED", "Rejected"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="distributor_profile",
        null=True,
        blank=True,
    )

    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    territory = models.CharField(max_length=150, blank=True)

    commission_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0")),
            MaxValueValidator(Decimal("100")),
        ],
    )

    upfront_payment_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0")),
            MaxValueValidator(Decimal("100")),
        ],
        help_text=(
            "How much of a Purchase Order's total this Distributor must "
            "pay (and have it confirmed by the Owner) before the Owner "
            "can ship anything on it. 0 = pay only after receiving, "
            "100 = pay in full before shipping, anything in between is "
            "a split (e.g. 30 = 30% before shipping, 70% after receiving)."
        ),
    )

    approval_status = models.CharField(
        max_length=12,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
    )

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approved_distributor_profiles",
        null=True,
        blank=True,
    )

    approved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    objects = DistributorProfileQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def clean(self):
        super().clean()

        self.name = self.name.strip()

        if not self.name:
            raise ValidationError(
                {"name": "Distributor name is required."}
            )

        if self.user_id and self.user.role != "DISTRIBUTOR":
            raise ValidationError(
                {
                    "user": (
                        "The linked user must be "
                        "a Distributor."
                    )
                }
            )

        if self.approval_status == self.ApprovalStatus.APPROVED:
            if not self.user_id:
                raise ValidationError(
                    {
                        "user": (
                            "An approved Distributor "
                            "must have a user."
                        )
                    }
                )

            if not self.approved_by_id or not self.approved_at:
                raise ValidationError(
                    "Approved Distributor profiles "
                    "require approval metadata."
                )

    def __str__(self):
        return (
            f"{self.name} - "
            f"{self.get_approval_status_display()}"
        )
