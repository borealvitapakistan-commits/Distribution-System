from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import AuditedModel, TimeStampedModel, UUIDModel
from apps.core.querysets import OwnerManagedQuerySet

from .querysets import StockBalanceQuerySet


class StockMovement(AuditedModel):
    """Immutable ledger row. Every stock change — receiving from a
    Manufacturer, giving to a Distributor, a manual correction — is one
    row here. Corrections are new rows, never edits."""

    class MovementType(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        TRANSFER = "TRANSFER", "Transfer"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="stock_movements",
    )

    from_location = models.ForeignKey(
        "warehouse.Location",
        on_delete=models.PROTECT,
        related_name="movements_out",
        null=True,
        blank=True,
    )

    to_location = models.ForeignKey(
        "warehouse.Location",
        on_delete=models.PROTECT,
        related_name="movements_in",
        null=True,
        blank=True,
    )

    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )

    movement_type = models.CharField(
        max_length=20,
        choices=MovementType.choices,
    )

    reference = models.CharField(max_length=255, blank=True)

    objects = OwnerManagedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        super().clean()

        if not self.from_location_id and not self.to_location_id:
            raise ValidationError(
                "A movement needs at least one location."
            )

        if (
            self.from_location_id
            and self.from_location_id == self.to_location_id
        ):
            raise ValidationError(
                "Source and destination locations must differ."
            )

    def save(self, *args, **kwargs):
        exists = (
            self.pk
            and type(self).objects.filter(pk=self.pk).exists()
        )

        if exists:
            raise ValidationError(
                "Stock movements are immutable."
            )

        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Stock movements cannot be deleted."
        )

    def __str__(self):
        return (
            f"{self.get_movement_type_display()} - "
            f"{self.product.sku} - {self.quantity}"
        )


class StockBalance(UUIDModel, TimeStampedModel):
    """Derived cache: current quantity of a product at a location.

    Never hand-edited — only written by
    apps.inventory.services.post_stock_movement, and always reconstructable
    by summing StockMovement rows for the same product/location.
    """

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="stock_balances",
    )

    location = models.ForeignKey(
        "warehouse.Location",
        on_delete=models.PROTECT,
        related_name="stock_balances",
    )

    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
    )

    objects = StockBalanceQuerySet.as_manager()

    class Meta:
        ordering = ["product__name", "location__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "location"],
                name="unique_stock_balance_per_product_location",
            ),
        ]

    def __str__(self):
        return f"{self.product.sku} @ {self.location.code}: {self.quantity}"
