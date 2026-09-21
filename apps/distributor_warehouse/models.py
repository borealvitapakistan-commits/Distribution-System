from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import AuditedModel

from .querysets import DistributorOwnedQuerySet


class DistributorInventory(AuditedModel):
    """A region/city grouping of a Distributor's own warehouses (e.g.
    "Lahore"). The Distributor-side mirror of the Owner's Inventory
    (region) concept — scoped to one Distributor, with no relationship
    to the Owner's own records."""

    distributor_profile = models.ForeignKey(
        "distributors.DistributorProfile",
        on_delete=models.PROTECT,
        related_name="inventories",
    )

    code = models.CharField(max_length=50)
    name = models.CharField(max_length=200)
    active = models.BooleanField(default=True)

    objects = DistributorOwnedQuerySet.as_manager()

    class Meta:
        ordering = ["name", "code"]
        verbose_name_plural = "distributor inventories"
        constraints = [
            models.UniqueConstraint(
                fields=["distributor_profile", "code"],
                name="unique_distributor_inventory_code",
            ),
        ]

    def clean(self):
        super().clean()

        self.code = self.code.strip().upper()
        self.name = self.name.strip()

        if not self.code:
            raise ValidationError({"code": "Inventory code is required."})

        if not self.name:
            raise ValidationError({"name": "Inventory name is required."})

    def __str__(self):
        return f"{self.code} - {self.name}"


class DistributorLocation(AuditedModel):
    """One of a Distributor's own physical warehouses, or their holding
    pool for stock that's arrived but hasn't been placed yet. The
    Distributor-side mirror of the Owner's Location concept — scoped to
    one Distributor, with no relationship to the Owner's own warehouses."""

    class LocationType(models.TextChoices):
        WAREHOUSE = "WAREHOUSE", "Warehouse"
        UNALLOCATED = "UNALLOCATED", "Unallocated"

    distributor_profile = models.ForeignKey(
        "distributors.DistributorProfile",
        on_delete=models.PROTECT,
        related_name="distributor_locations",
    )

    code = models.CharField(max_length=50)
    name = models.CharField(max_length=200)
    location_type = models.CharField(
        max_length=20,
        choices=LocationType.choices,
    )
    distributor_inventory = models.ForeignKey(
        DistributorInventory,
        on_delete=models.PROTECT,
        related_name="warehouses",
        null=True,
        blank=True,
        help_text="Which region this warehouse belongs to. Required for a warehouse.",
    )
    address = models.TextField(blank=True)
    area_square_feet = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    capacity_units = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    active = models.BooleanField(default=True)

    objects = DistributorOwnedQuerySet.as_manager()

    class Meta:
        ordering = ["name", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["distributor_profile", "code"],
                name="unique_distributor_location_code",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(area_square_feet__gte=0)
                    & models.Q(capacity_units__gte=0)
                ),
                name="distributor_location_nonnegative_capacity",
            ),
        ]

    def clean(self):
        super().clean()

        self.code = self.code.strip().upper()
        self.name = self.name.strip()

        if not self.code:
            raise ValidationError({"code": "Location code is required."})

        if not self.name:
            raise ValidationError({"name": "Location name is required."})

        if self.location_type == self.LocationType.WAREHOUSE:
            if not self.distributor_inventory_id:
                raise ValidationError(
                    {
                        "distributor_inventory": (
                            "A warehouse must belong to an Inventory (region)."
                        )
                    }
                )

            if (
                self.distributor_inventory_id
                and self.distributor_profile_id
                and self.distributor_inventory.distributor_profile_id
                != self.distributor_profile_id
            ):
                raise ValidationError(
                    {
                        "distributor_inventory": (
                            "That region belongs to a different distributor."
                        )
                    }
                )
        elif (
            self.location_type != self.LocationType.UNALLOCATED
            and self.distributor_inventory_id
        ):
            raise ValidationError(
                {
                    "distributor_inventory": (
                        "Only a warehouse can belong to an Inventory."
                    )
                }
            )

    def __str__(self):
        return f"{self.code} - {self.name}"
