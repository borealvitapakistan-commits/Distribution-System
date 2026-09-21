from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import AuditedModel

from .querysets import PurchaseOrderQuerySet


class PurchaseOrder(AuditedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SHIPPED = "SHIPPED", "Shipped"
        RECEIVED = "RECEIVED", "Received"
        DECLINED = "DECLINED", "Declined"

    distributor_profile = models.ForeignKey(
        "distributors.DistributorProfile",
        on_delete=models.PROTECT,
        related_name="purchase_orders",
    )

    po_number = models.CharField(max_length=20, unique=True, editable=False)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    owner_comment = models.TextField(blank=True)
    tax_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    shipping_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    invoiced_at = models.DateTimeField(null=True, blank=True)
    owner_viewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the Owner first opened this order. Null means it's new.",
    )

    objects = PurchaseOrderQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    @property
    def subtotal(self):
        total = sum(
            (item.line_total for item in self.items.all()),
            Decimal("0.00"),
        )
        return total.quantize(Decimal("0.01"))

    @property
    def tax_amount(self):
        return (self.subtotal * self.tax_percentage / Decimal("100")).quantize(
            Decimal("0.01")
        )

    @property
    def grand_total(self):
        return self.subtotal + self.tax_amount + self.shipping_amount

    @property
    def total_paid(self):
        """Only payments the Owner has confirmed count — a Distributor's
        own claim isn't enough on its own."""
        return sum(
            (
                payment.amount
                for payment in self.payments.all()
                if payment.status == PurchaseOrderPayment.Status.CONFIRMED
            ),
            Decimal("0.00"),
        )

    @property
    def is_fully_paid(self):
        return self.total_paid >= self.grand_total

    @property
    def required_upfront_amount(self):
        pct = self.distributor_profile.upfront_payment_percentage
        return (self.grand_total * pct / Decimal("100")).quantize(Decimal("0.01"))

    @property
    def upfront_amount_satisfied(self):
        return self.total_paid >= self.required_upfront_amount

    def __str__(self):
        return f"{self.po_number} - {self.distributor_profile.name} - {self.status}"


class PurchaseOrderItem(AuditedModel):
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.PROTECT,
        related_name="items",
    )

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="purchase_order_items",
    )

    quantity_requested = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )

    unit_price = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )

    quantity_shipped = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[MinValueValidator(Decimal("0"))],
    )

    quantity_received = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        ordering = ["product__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["purchase_order", "product"],
                name="unique_product_per_purchase_order",
            ),
        ]

    @property
    def quantity_to_ship_remaining(self):
        return self.quantity_requested - self.quantity_shipped

    @property
    def quantity_to_receive_remaining(self):
        return self.quantity_shipped - self.quantity_received

    @property
    def line_total(self):
        return (self.quantity_requested * self.unit_price).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.product.sku} - {self.quantity_requested}"


class PurchaseOrderPayment(AuditedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending confirmation"
        CONFIRMED = "CONFIRMED", "Confirmed"
        REJECTED = "REJECTED", "Rejected"

    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.PROTECT,
        related_name="payments",
    )

    amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    paid_at = models.DateField()

    proof = models.FileField(upload_to="purchase_order_payments/")

    note = models.CharField(max_length=255, blank=True)

    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
        help_text=(
            "The Distributor's own claim that they paid isn't enough — "
            "this only counts toward the Purchase Order's paid total, "
            "and unblocks shipping, once the Owner confirms it."
        ),
    )

    confirmed_at = models.DateTimeField(null=True, blank=True)

    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="confirmed_purchase_order_payments",
        null=True,
        blank=True,
    )

    rejection_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.purchase_order.po_number} - {self.amount}"
