from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import AuditedModel

from .querysets import StockRequestQuerySet


class StockRequest(AuditedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PARTIALLY_FULFILLED = "PARTIALLY_FULFILLED", "Partially fulfilled"
        FULFILLED = "FULFILLED", "Fulfilled"
        DECLINED = "DECLINED", "Declined"

    distributor_profile = models.ForeignKey(
        "distributors.DistributorProfile",
        on_delete=models.PROTECT,
        related_name="stock_requests",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    owner_comment = models.TextField(blank=True)

    objects = StockRequestQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Request {self.pk} - {self.distributor_profile.name} - {self.status}"


class StockRequestItem(AuditedModel):
    request = models.ForeignKey(
        StockRequest,
        on_delete=models.PROTECT,
        related_name="items",
    )

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="stock_request_items",
    )

    quantity_requested = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )

    quantity_fulfilled = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        ordering = ["product__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["request", "product"],
                name="unique_product_per_stock_request",
            ),
        ]

    @property
    def quantity_remaining(self):
        return self.quantity_requested - self.quantity_fulfilled

    def __str__(self):
        return f"{self.product.sku} - {self.quantity_requested}"
