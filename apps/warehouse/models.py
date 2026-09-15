from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.core.models import AuditedModel
from apps.core.querysets import OwnerManagedQuerySet


class Location(AuditedModel):
    class LocationType(models.TextChoices):
        OWN = "OWN", "Owned"
        SUPPLIER = "SUPPLIER", "Supplier"
        DISTRIBUTOR = "DISTRIBUTOR", "Distributor"
        CUSTOMER = "CUSTOMER", "Customer"
        SHOPIFY = "SHOPIFY", "Shopify"

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)

    location_type = models.CharField(
        max_length=20,
        choices=LocationType.choices,
    )

    manufacturer = models.ForeignKey(
        "manufacturers.Manufacturer",
        on_delete=models.PROTECT,
        related_name="locations",
        null=True,
        blank=True,
    )

    distributor_profile = models.ForeignKey(
        "distributors.DistributorProfile",
        on_delete=models.PROTECT,
        related_name="locations",
        null=True,
        blank=True,
    )

    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.PROTECT,
        related_name="locations",
        null=True,
        blank=True,
    )

    on_book = models.BooleanField(default=True)
    is_physical = models.BooleanField(default=True)
    is_sellable = models.BooleanField(default=False)

    address = models.TextField(blank=True)

    area_square_feet = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0"))
        ],
    )

    capacity_units = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[
            MinValueValidator(Decimal("0"))
        ],
    )

    active = models.BooleanField(default=True)

    objects = OwnerManagedQuerySet.as_manager()

    class Meta:
        ordering = ["name", "code"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(area_square_feet__gte=0)
                    & Q(capacity_units__gte=0)
                ),
                name="location_nonnegative_capacity",
            ),
        ]

    def clean(self):
        super().clean()

        self.code = self.code.strip().upper()
        self.name = self.name.strip()

        if not self.code:
            raise ValidationError(
                {"code": "Location code is required."}
            )

        if not self.name:
            raise ValidationError(
                {"name": "Location name is required."}
            )

        fixed_flags = {
            self.LocationType.OWN: (
                True,
                True,
                True,
            ),
            self.LocationType.SHOPIFY: (
                True,
                True,
                True,
            ),
            self.LocationType.SUPPLIER: (
                False,
                False,
                False,
            ),
            self.LocationType.CUSTOMER: (
                False,
                False,
                False,
            ),
        }

        if self.location_type in fixed_flags:
            expected = fixed_flags[self.location_type]

            actual = (
                self.on_book,
                self.is_physical,
                self.is_sellable,
            )

            if actual != expected:
                raise ValidationError(
                    "Location flags do not match "
                    "the selected location type."
                )

        elif self.location_type == self.LocationType.DISTRIBUTOR:
            if not self.on_book or not self.is_physical:
                raise ValidationError(
                    "Distributor locations must be "
                    "on-book and physical."
                )

        if self.location_type == self.LocationType.SUPPLIER:
            if not self.manufacturer_id:
                raise ValidationError(
                    {
                        "manufacturer": (
                            "Supplier locations require "
                            "a Manufacturer."
                        )
                    }
                )
        elif self.manufacturer_id:
            raise ValidationError(
                {
                    "manufacturer": (
                        "This location type cannot be "
                        "linked to a Manufacturer."
                    )
                }
            )

        if self.location_type == self.LocationType.DISTRIBUTOR:
            if not self.distributor_profile_id:
                raise ValidationError(
                    {
                        "distributor_profile": (
                            "Distributor locations require "
                            "a Distributor."
                        )
                    }
                )
        elif self.distributor_profile_id:
            raise ValidationError(
                {
                    "distributor_profile": (
                        "This location type cannot be "
                        "linked to a Distributor."
                    )
                }
            )

        if self.location_type == self.LocationType.CUSTOMER:
            pass
        elif self.customer_id:
            raise ValidationError(
                {
                    "customer": (
                        "This location type cannot be "
                        "linked to a Customer."
                    )
                }
            )

    def __str__(self):
        return f"{self.code} - {self.name}"
