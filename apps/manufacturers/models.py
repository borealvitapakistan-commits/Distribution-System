from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import AuditedModel
from apps.core.payments import AdvanceFinalPaymentsMixin, PaymentKind
from apps.core.querysets import OwnerManagedQuerySet
from apps.products.models import BottleSize


class Manufacturer(AuditedModel):
    name = models.CharField(max_length=200, unique=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    logo = models.ImageField(upload_to="manufacturers/", null=True, blank=True)
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


class ManufacturerOrder(AdvanceFinalPaymentsMixin, AuditedModel):
    class Status(models.TextChoices):
        QUOTE = "QUOTE", "Request to Quote"
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
    terms = models.TextField(
        blank=True,
        help_text="Terms and conditions printed on the Request to Quote / Purchase Order.",
    )

    quote_file = models.FileField(
        upload_to="manufacturer_order_quotes/",
        null=True,
        blank=True,
        help_text="The Manufacturer's reply to our Request to Quote, with their real prices.",
    )
    quoted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the Manufacturer's quoted prices were entered.",
    )
    po_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the Request to Quote became a Purchase Order.",
    )

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

    invoice_number = models.CharField(
        max_length=100,
        blank=True,
        help_text="Our own invoice number, generated on receipt: INV-<BRAND>-0001.",
    )
    supplier_invoice_ref = models.CharField(
        max_length=100,
        blank=True,
        help_text="The invoice number printed on the Manufacturer's own invoice, if any.",
    )

    invoice_approved_at = models.DateTimeField(null=True, blank=True)
    pays_advance = models.BooleanField(
        null=True,
        blank=True,
        help_text=(
            "The Owner's answer to \"Are you paying in advance?\" right "
            "after placing the order. Blank until answered."
        ),
    )

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
    def is_quote(self):
        return self.status == self.Status.QUOTE

    @property
    def document_title(self):
        return "Request to Quote" if self.is_quote else "Purchase Order"

    @property
    def purchase_order_date(self):
        """Older orders were placed directly as Purchase Orders, before
        the Request to Quote step existed."""
        if self.is_quote:
            return None
        return self.po_sent_at or self.created_at

    @property
    def has_price_changes(self):
        return any(item.price_changed for item in self.items.all())

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

    bottle_size = models.PositiveSmallIntegerField(
        choices=BottleSize.choices,
        null=True,
        blank=True,
        help_text="Capsules per bottle. Blank for products not sold by capsule count.",
    )

    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
        help_text="Number of bottles (units).",
    )

    requested_unit_price = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="The price we asked for on the Request to Quote (blank = asked them to quote).",
    )

    unit_price = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="The current agreed price per bottle. Blank only on a Request to Quote.",
    )

    class Meta:
        ordering = ["product__name", "bottle_size"]
        constraints = [
            models.UniqueConstraint(
                fields=["order", "product", "bottle_size"],
                name="unique_product_size_per_manufacturer_order",
            ),
        ]

    @property
    def line_total(self):
        if self.unit_price is None:
            return Decimal("0.00")
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))

    @property
    def requested_price_given(self):
        """Whether we asked for a price on the Request to Quote. A 0 counts
        as not given — nobody buys at zero, it means "please quote"."""
        return self.requested_unit_price not in (None, Decimal("0"))

    @property
    def price_highlight(self):
        """How the line is coloured once the Manufacturer's price is in:
        "changed" (orange) — we asked for a price and theirs differs;
        "new" (yellow) — we left the price empty and they filled it in;
        "" (white) — their price matches ours, or nothing is quoted yet
        (a 0 counts as not quoted)."""
        if self.unit_price in (None, Decimal("0")):
            return ""
        if not self.requested_price_given:
            return "new"
        if self.unit_price != self.requested_unit_price:
            return "changed"
        return ""

    @property
    def price_changed(self):
        """True for any highlighted line, orange or yellow."""
        return bool(self.price_highlight)

    def __str__(self):
        size = f" ({self.get_bottle_size_display()})" if self.bottle_size else ""
        return f"{self.product.sku}{size} - {self.quantity}"


class ManufacturerOrderPayment(AuditedModel):
    """Owner-entered only — the Manufacturer isn't a system user, so
    there's no separate claim/confirm step like with Distributor
    payments. What the Owner enters here is authoritative.

    An order is paid for in up to two parts: an ADVANCE right after the
    order is placed and a FINAL payment for whatever is left once the
    goods arrive — either part can be the whole amount or nothing."""

    Kind = PaymentKind

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

    kind = models.CharField(
        max_length=10,
        choices=Kind.choices,
        default=Kind.FINAL,
    )

    proof = models.FileField(
        upload_to="manufacturer_order_payments/",
        null=True,
        blank=True,
        help_text=(
            "Bank slip, screenshot, etc. Required for every new payment; "
            "only blank on payments recorded before proofs existed."
        ),
    )

    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["paid_at", "created_at"]

    def __str__(self):
        return f"{self.order.po_number} - {self.get_kind_display()} - {self.amount}"


class OrderRevision(AuditedModel):
    """One step in the back-and-forth on a Manufacturer order — our
    Request to Quote, each reply the Manufacturer sends, each counter-offer
    we make, every version of the Purchase Order and the final invoice.

    Each one is a frozen copy of the lines as they stood at that moment
    plus the message that went with it, so the whole conversation (who
    asked for what price, when, and what was finally agreed) can always
    be traced. Nothing here changes once it's saved; the live prices
    are still on ManufacturerOrderItem."""

    class Stage(models.TextChoices):
        REQUEST = "REQUEST", "Request to Quote"
        QUOTE = "QUOTE", "Manufacturer's quote"
        COUNTER = "COUNTER", "Our counter-offer"
        PURCHASE_ORDER = "PURCHASE_ORDER", "Purchase Order"
        INVOICE = "INVOICE", "Manufacturer's invoice"

    FROM_MANUFACTURER = {Stage.QUOTE, Stage.INVOICE}

    order = models.ForeignKey(
        ManufacturerOrder,
        on_delete=models.PROTECT,
        related_name="revisions",
    )

    number = models.PositiveIntegerField()

    stage = models.CharField(max_length=20, choices=Stage.choices)

    message = models.TextField(
        blank=True,
        help_text="What was said with this step — our note, or what the Manufacturer replied.",
    )

    attachment = models.FileField(
        upload_to="manufacturer_order_revisions/",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["order", "number"]
        constraints = [
            models.UniqueConstraint(
                fields=["order", "number"],
                name="unique_revision_number_per_manufacturer_order",
            ),
        ]

    @property
    def from_manufacturer(self):
        return self.stage in self.FROM_MANUFACTURER

    @property
    def total(self):
        total = sum(
            (line.line_total for line in self.lines.all() if not line.removed),
            Decimal("0.00"),
        )
        return total.quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.order.po_number} #{self.number} - {self.get_stage_display()}"


class OrderRevisionLine(AuditedModel):
    revision = models.ForeignKey(
        OrderRevision,
        on_delete=models.CASCADE,
        related_name="lines",
    )

    item = models.ForeignKey(
        ManufacturerOrderItem,
        on_delete=models.SET_NULL,
        related_name="revision_lines",
        null=True,
        blank=True,
        help_text="The live order line; blank once that line was dropped from the order.",
    )

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="+",
    )

    bottle_size = models.PositiveSmallIntegerField(
        choices=BottleSize.choices,
        null=True,
        blank=True,
    )

    quantity = models.DecimalField(max_digits=18, decimal_places=4)

    unit_price = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Blank = no price given (please quote).",
    )

    removed = models.BooleanField(
        default=False,
        help_text="This line was dropped from the order at this step.",
    )

    class Meta:
        ordering = ["product__name", "bottle_size"]

    @property
    def line_key(self):
        return (self.product_id, self.bottle_size)

    @property
    def line_total(self):
        if self.unit_price is None:
            return Decimal("0.00")
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.revision} - {self.product_id} - {self.unit_price}"
