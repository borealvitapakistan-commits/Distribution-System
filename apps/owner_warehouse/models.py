from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from apps.core.models import AuditedModel
from apps.core.querysets import OwnerManagedQuerySet


class Inventory(AuditedModel):
    """A region/city grouping of warehouses (e.g. "Lahore"). Free-text
    name — deliberately not a fixed list of cities."""

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    active = models.BooleanField(default=True)

    objects = OwnerManagedQuerySet.as_manager()

    class Meta:
        ordering = ["name", "code"]
        verbose_name_plural = "inventories"

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


class Location(AuditedModel):
    class LocationType(models.TextChoices):
        OWN = "OWN", "Owned"
        SUPPLIER = "SUPPLIER", "Supplier"
        SHOPIFY = "SHOPIFY", "Shopify"
        TRANSIT = "TRANSIT", "In Transit"
        UNALLOCATED = "UNALLOCATED", "Unallocated"

    # (on_book, is_physical, is_sellable) per type; None means the Owner
    # chooses it — an owned/Shopify warehouse can be held back from sale.
    FIXED_FLAGS = {
        LocationType.OWN: (True, True, None),
        LocationType.SHOPIFY: (True, True, None),
        LocationType.TRANSIT: (True, True, False),
        LocationType.UNALLOCATED: (True, True, False),
        LocationType.SUPPLIER: (False, False, False),
    }

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    location_type = models.CharField(
        max_length=20,
        choices=LocationType.choices,
    )
    inventory = models.ForeignKey(
        Inventory,
        on_delete=models.PROTECT,
        related_name="warehouses",
        null=True,
        blank=True,
        help_text="Which region this warehouse belongs to. Required for owned warehouses.",
    )
    manufacturer = models.ForeignKey(
        "manufacturers.Manufacturer",
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

        if self.location_type in self.FIXED_FLAGS:
            expected = self.FIXED_FLAGS[self.location_type]

            actual = (
                self.on_book,
                self.is_physical,
                self.is_sellable,
            )

            if any(
                want is not None and have != want
                for have, want in zip(actual, expected)
            ):
                raise ValidationError(
                    "Location flags do not match "
                    "the selected location type."
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

        if self.location_type == self.LocationType.OWN:
            if not self.inventory_id:
                raise ValidationError(
                    {
                        "inventory": (
                            "An owned warehouse must belong to an Inventory (region)."
                        )
                    }
                )
        elif (
            self.location_type != self.LocationType.UNALLOCATED
            and self.inventory_id
        ):
            raise ValidationError(
                {
                    "inventory": (
                        "This location type cannot be linked to an Inventory."
                    )
                }
            )

    def __str__(self):
        return f"{self.code} - {self.name}"
