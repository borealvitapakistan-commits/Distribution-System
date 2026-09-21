from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import AuditedModel, TimeStampedModel, UUIDModel
from apps.core.querysets import OwnerManagedQuerySet

from .querysets import StockBalanceQuerySet, StockBatchQuerySet


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
        "owner_warehouse.Location",
        on_delete=models.PROTECT,
        related_name="movements_out",
        null=True,
        blank=True,
    )

    to_location = models.ForeignKey(
        "owner_warehouse.Location",
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
        "StockBatch",
        on_delete=models.PROTECT,
        related_name="movements",
        null=True,
        blank=True,
        help_text=(
            "For a RECEIVED movement, the batch it created. For stock "
            "leaving an owned warehouse, the batch it was drawn from."
        ),
    )

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
    apps.owner_inventory.services.post_stock_movement, and always reconstructable
    by summing StockMovement rows for the same product/location.
    """

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="stock_balances",
    )

    location = models.ForeignKey(
        "owner_warehouse.Location",
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


class StockBatch(AuditedModel):
    """One lot of stock received into an owned warehouse on a given date.

    Herbal products expire, so stock isn't just one running number per
    product/location — each Add Inventory is its own dated, expirable lot.
    Shipping out draws down a specific batch's quantity_remaining (FEFO by
    default), so older/soon-to-expire stock gets suggested first. Batches
    only exist for locations the Owner directly manages (own warehouses,
    Shopify, Transit) — once stock reaches a Distributor, it's tracked in
    their own, separate DistributorStockBatch instead.
    """

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="stock_batches",
    )

    location = models.ForeignKey(
        "owner_warehouse.Location",
        on_delete=models.PROTECT,
        related_name="stock_batches",
    )

    source_batch = models.ForeignKey(
        "batches.Batch",
        on_delete=models.PROTECT,
        related_name="stock_batches",
        null=True,
        blank=True,
        help_text=(
            "The canonical Batch this lot traces back to, when it came "
            "from a Manufacturer Purchase Order. Not set for stock added "
            "manually (e.g. the Add Inventory form)."
        ),
    )

    batch_number = models.CharField(
        max_length=100,
        blank=True,
        help_text=(
            "Your own lot/batch label, e.g. BV-GL-AML-001 "
            "(Brand-Manufacturer-Product-Sequence). Not unique on its "
            "own — every split of this same lot across warehouses keeps "
            "the same batch number, so it always traces back to one "
            "delivery."
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

    objects = StockBatchQuerySet.as_manager()

    class Meta:
        ordering = ["expiry_date", "received_date", "created_at"]

    def clean(self):
        super().clean()

        from apps.owner_warehouse.models import Location

        if self.location_id and self.location.location_type not in (
            Location.LocationType.OWN,
            Location.LocationType.SHOPIFY,
            Location.LocationType.UNALLOCATED,
            Location.LocationType.TRANSIT,
        ):
            raise ValidationError(
                "Batches can only be tracked in one of the Owner's own "
                "warehouses, the Shopify location, Transit, or as "
                "unallocated stock."
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
