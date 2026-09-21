from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import AuditedModel
from apps.core.querysets import OwnerManagedQuerySet


class Batch(AuditedModel):
    """The canonical lot record for one Manufacturer Purchase Order line.

    Created the moment the Owner places the order (status PENDING) —
    before the Manufacturer has shipped anything. It's the single
    identity a lot carries for its whole life: through invoice approval
    (RECEIVED) into the Owner's own warehouse batches, or, if the line
    never gets fulfilled, to CANCELLED.

    Deliberately has no direct link to Product — a Batch only exists
    because of a Purchase Order line, so its product is always reached
    through manufacturer_order_item.product rather than duplicated here.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RECEIVED = "RECEIVED", "Received"
        CANCELLED = "CANCELLED", "Cancelled"

    code = models.CharField(max_length=50, unique=True, editable=False)

    manufacturer_order_item = models.OneToOneField(
        "manufacturers.ManufacturerOrderItem",
        on_delete=models.PROTECT,
        related_name="batch",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    expiry_date = models.DateField(null=True, blank=True)

    received_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    objects = OwnerManagedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "batches"

    def clean(self):
        super().clean()

        if (
            self.expiry_date
            and self.received_at
            and self.expiry_date < self.received_at.date()
        ):
            raise ValidationError(
                {"expiry_date": "Expiry date cannot be before the received date."}
            )

    def __str__(self):
        return self.code
