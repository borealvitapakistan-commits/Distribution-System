from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import AuditedModel, TimeStampedModel, UUIDModel

from .querysets import DistributorStockBatchQuerySet, DistributorStockQuerySet


class DistributorStockMovement(AuditedModel):
    """Immutable ledger row for one Distributor's own stock — receiving
    a shipment from the Owner, allocating it to a warehouse, a manual
    correction. The Distributor-side mirror of the Owner's StockMovement,
    with no FK relationship to the Owner's own ledger."""

    class MovementType(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        TRANSFER = "TRANSFER", "Transfer"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"

    distributor_profile = models.ForeignKey(
        "distributors.DistributorProfile",
        on_delete=models.PROTECT,
        related_name="stock_movements",
    )

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="distributor_stock_movements",
    )

    from_location = models.ForeignKey(
        "distributor_warehouse.DistributorLocation",
        on_delete=models.PROTECT,
        related_name="movements_out",
        null=True,
        blank=True,
    )

    to_location = models.ForeignKey(
        "distributor_warehouse.DistributorLocation",
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

    batch = models.ForeignKey(
        "DistributorStockBatch",
        on_delete=models.PROTECT,
        related_name="movements",
        null=True,
        blank=True,
        help_text=(
            "For a RECEIVED movement, the batch it created. For stock "
            "leaving a warehouse, the batch it was drawn from."
        ),
    )

    objects = DistributorStockQuerySet.as_manager()

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


class DistributorStockBalance(UUIDModel, TimeStampedModel):
    """Derived cache: current quantity of a product at one of a
    Distributor's own locations. Never hand-edited — only written by
    apps.distributor_inventory.services.post_stock_movement."""

    distributor_profile = models.ForeignKey(
        "distributors.DistributorProfile",
        on_delete=models.PROTECT,
        related_name="stock_balances",
    )

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="distributor_stock_balances",
    )

    location = models.ForeignKey(
        "distributor_warehouse.DistributorLocation",
        on_delete=models.PROTECT,
        related_name="stock_balances",
    )

    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
    )

    objects = DistributorStockQuerySet.as_manager()

    class Meta:
        ordering = ["product__name", "location__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "location"],
                name="unique_distributor_stock_balance_per_product_location",
            ),
        ]

    def __str__(self):
        return f"{self.product.sku} @ {self.location.code}: {self.quantity}"


class DistributorStockBatch(AuditedModel):
    """One lot of stock received into a Distributor's own warehouse (or
    their unallocated holding pool) on a given date. Mirrors the Owner's
    StockBatch — FEFO-ordered, expirable, its own dated lot per receipt —
    scoped to one Distributor with no relationship to the Owner's own
    batches."""

    distributor_profile = models.ForeignKey(
        "distributors.DistributorProfile",
        on_delete=models.PROTECT,
        related_name="stock_batches",
    )

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="distributor_stock_batches",
    )

    location = models.ForeignKey(
        "distributor_warehouse.DistributorLocation",
        on_delete=models.PROTECT,
        related_name="stock_batches",
    )

    batch_number = models.CharField(
        max_length=100,
        blank=True,
        help_text=(
            "Lot/batch label. Not unique on its own — every split of "
            "this same lot across warehouses keeps the same batch "
            "number, so it always traces back to one delivery."
        ),
    )

    received_date = models.DateField(default=date.today)

    expiry_date = models.DateField(null=True, blank=True)

    quantity_received = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )

    quantity_remaining = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0"))],
    )

    reference = models.CharField(max_length=255, blank=True)

    objects = DistributorStockBatchQuerySet.as_manager()

    class Meta:
        ordering = ["expiry_date", "received_date", "created_at"]
        verbose_name_plural = "distributor stock batches"

    def clean(self):
        super().clean()

        if (
            self.location_id
            and self.distributor_profile_id
            and self.location.distributor_profile_id != self.distributor_profile_id
        ):
            raise ValidationError(
                "The selected warehouse belongs to a different distributor."
            )

        if (
            self.expiry_date
            and self.received_date
            and self.expiry_date < self.received_date
        ):
            raise ValidationError(
                {
                    "expiry_date": (
                        "Expiry date cannot be before the received date."
                    )
                }
            )

        if (
            self.quantity_received is not None
            and self.quantity_remaining is not None
            and self.quantity_remaining > self.quantity_received
        ):
            raise ValidationError(
                {
                    "quantity_remaining": (
                        "Remaining quantity cannot exceed the quantity "
                        "originally received."
                    )
                }
            )

    def __str__(self):
        label = f"{self.batch_number} - " if self.batch_number else ""
        return (
            f"{label}{self.product.sku} @ {self.location.code} - "
            f"received {self.received_date} "
            f"({self.quantity_remaining} remaining)"
        )
