from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import AuditedModel
from apps.core.querysets import OwnerManagedQuerySet


class Manufacturer(AuditedModel):
    name = models.CharField(max_length=200, unique=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    upfront_payment_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0")),
            MaxValueValidator(Decimal("100")),
        ],
        help_text=(
            "How much of an order's total is owed before shipping (from "
            "them), vs after receiving. 0 = pay only after receiving, "
            "100 = pay in full before shipping, anything in between is "
            "a split."
        ),
    )

    objects = OwnerManagedQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def clean(self):
        super().clean()

        self.name = self.name.strip()

        if not self.name:
            raise ValidationError(
                {"name": "Manufacturer name is required."}
            )

    def __str__(self):
        return self.name


class ManufacturerOrder(AuditedModel):
    class Status(models.TextChoices):
        SENT = "SENT", "Sent"
        RECEIVED = "RECEIVED", "Received"
        GOOD = "GOOD", "Good — no issues"
        DISPUTED = "DISPUTED", "Under discussion"
        REFUNDED = "REFUNDED", "Refunded"

    manufacturer = models.ForeignKey(
        Manufacturer,
        on_delete=models.PROTECT,
        related_name="orders",
    )

    brand = models.ForeignKey(
        "core.Brand",
        on_delete=models.PROTECT,
        related_name="manufacturer_orders",
        null=True,
        blank=True,
    )

    po_number = models.CharField(max_length=20, unique=True, editable=False)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SENT,
    )

    owner_note = models.TextField(blank=True)
    outcome_note = models.TextField(
        blank=True,
        help_text="Why this was disputed or refunded.",
    )

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

    received_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the goods physically arrived from the Manufacturer.",
    )

    invoice_file = models.FileField(
        upload_to="manufacturer_order_invoices/",
        null=True,
        blank=True,
        help_text="The Manufacturer's own invoice document, as they gave it to us.",
    )

    invoice_number = models.CharField(max_length=100, blank=True)

    invoice_approved_at = models.DateTimeField(null=True, blank=True)

    invoice_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approved_manufacturer_order_invoices",
        null=True,
        blank=True,
    )

    objects = OwnerManagedQuerySet.as_manager()

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
        return sum(
            (payment.amount for payment in self.payments.all()),
            Decimal("0.00"),
        )

    @property
    def is_fully_paid(self):
        return self.total_paid >= self.grand_total

    @property
    def required_upfront_amount(self):
        pct = self.manufacturer.upfront_payment_percentage
        return (self.grand_total * pct / Decimal("100")).quantize(Decimal("0.01"))

    @property
    def upfront_amount_satisfied(self):
        return self.total_paid >= self.required_upfront_amount

    def __str__(self):
        return f"{self.po_number} - {self.manufacturer.name} - {self.status}"


class ManufacturerOrderItem(AuditedModel):
    order = models.ForeignKey(
        ManufacturerOrder,
        on_delete=models.PROTECT,
        related_name="items",
    )

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="manufacturer_order_items",
    )

    source_purchase_order_item = models.ForeignKey(
        "requests.PurchaseOrderItem",
        on_delete=models.SET_NULL,
        related_name="manufacturer_order_items",
        null=True,
        blank=True,
        help_text=(
            "The Distributor purchase order line this was ordered to "
            "restock, if it was created that way."
        ),
    )

    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )

    unit_price = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        ordering = ["product__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["order", "product"],
                name="unique_product_per_manufacturer_order",
            ),
        ]

    @property
    def line_total(self):
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.product.sku} - {self.quantity}"


class ManufacturerOrderPayment(AuditedModel):
    """Owner-entered only — the Manufacturer isn't a system user, so
    there's no separate claim/confirm step like with Distributor
    payments. What the Owner enters here is authoritative."""

    order = models.ForeignKey(
        ManufacturerOrder,
        on_delete=models.PROTECT,
        related_name="payments",
    )

    amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    paid_at = models.DateField()

    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.order.po_number} - {self.amount}"
