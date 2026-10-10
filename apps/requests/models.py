from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import AuditedModel
from apps.core.payments import AdvanceFinalPaymentsMixin, PaymentKind

from .querysets import PurchaseOrderQuerySet


class PurchaseOrder(AdvanceFinalPaymentsMixin, AuditedModel):
    """A Distributor asking the Owner for stock, in the same four stages
    as an order to a Manufacturer: the Distributor's Request to Quote, the
    Owner's quote (and any counter-offers back and forth), the Purchase
    Order once the Distributor accepts, and the Owner's invoice. Both
    sides work from their own panel — every step is kept in the order's
    conversation (PurchaseOrderRevision).

    Paid for the same way as an order to a Manufacturer — an optional
    advance right after placing it, and the rest once the goods arrive
    (see apps.core.payments) — except the Owner has to confirm each
    payment actually landed."""

    class Status(models.TextChoices):
        QUOTE = "QUOTE", "Request to Quote"
        PENDING = "PENDING", "Pending"
        PARTIALLY_SHIPPED = "PARTIALLY_SHIPPED", "Partially shipped"
        SHIPPED = "SHIPPED", "Shipped"
        RECEIVED = "RECEIVED", "Received"
        DECLINED = "DECLINED", "Declined"

    distributor_profile = models.ForeignKey(
        "distributors.DistributorProfile",
        on_delete=models.PROTECT,
        related_name="purchase_orders",
    )

    po_number = models.CharField(max_length=20, unique=True, editable=False)

    agreement = models.ForeignKey(
        "agreements.Agreement",
        on_delete=models.PROTECT,
        related_name="purchase_orders",
        null=True,
        blank=True,
        help_text=(
            "The signed agreement in force on the day the order was placed. "
            "Its discounts pre-filled each line. Blank = list prices."
        ),
    )
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
    quoted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the Owner first answered the Request to Quote with prices.",
    )
    po_placed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the Distributor accepted the quote and it became a Purchase Order.",
    )
    invoice_number = models.CharField(
        max_length=100,
        blank=True,
        help_text="The Owner's invoice number, given when the invoice is issued: SI-<BRAND>-0001.",
    )
    invoiced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the Owner issued the invoice.",
    )
    pays_advance = models.BooleanField(
        null=True,
        blank=True,
        help_text=(
            "The Distributor's answer to \"Are you paying in advance?\" "
            "right after placing the order. Blank until answered."
        ),
    )
    owner_viewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the Owner first opened this order. Null means it's new.",
    )
    owner_seen_revision = models.PositiveIntegerField(
        default=0,
        help_text="The last step of the conversation the Owner has seen.",
    )
    distributor_seen_revision = models.PositiveIntegerField(
        default=0,
        help_text="The last step of the conversation the Distributor has seen.",
    )

    objects = PurchaseOrderQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_quote(self):
        return self.status == self.Status.QUOTE

    @property
    def is_invoiced(self):
        return self.invoiced_at is not None

    @property
    def declined_as_quote(self):
        """Declined by the Owner before it ever became a Purchase Order."""
        return self.status == self.Status.DECLINED and self.po_placed_at is None

    @property
    def document_title(self):
        if self.is_quote or self.declined_as_quote:
            return "Quotation" if self.quoted_at else "Request to Quote"
        return "Invoice" if self.is_invoiced else "Purchase Order"

    @property
    def purchase_order_date(self):
        if self.is_quote or self.declined_as_quote:
            return None
        return self.po_placed_at or self.created_at

    @property
    def has_price_changes(self):
        return any(item.price_changed for item in self.items.all())

    @property
    def can_issue_invoice(self):
        """The invoice goes out once everything that will ship has shipped
        — every line sent in full or closed as unavailable (or the rest of
        the order declined)."""
        if self.is_quote or self.is_invoiced:
            return False
        items = list(self.items.all())
        if not any(item.quantity_shipped > 0 for item in items):
            return False
        return self.status == self.Status.DECLINED or all(
            item.quantity_to_ship_remaining <= 0 for item in items
        )

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

    def counted_payments(self):
        """Payments still standing — confirmed or awaiting the Owner's
        confirmation. A rejected one no longer counts toward what's been
        paid, so the Distributor can pay that part again."""
        return [
            payment
            for payment in self.payments.all()
            if payment.status != PurchaseOrderPayment.Status.REJECTED
        ]

    @property
    def is_open(self):
        return self.status not in (self.Status.RECEIVED, self.Status.DECLINED)

    @property
    def can_change(self):
        """A placed Purchase Order can still be changed by the Owner (as a
        new version) until anything ships or the invoice goes out."""
        return (
            self.status == self.Status.PENDING
            and not self.is_invoiced
            and all(item.quantity_shipped == 0 for item in self.items.all())
        )

    @property
    def awaiting_receipt(self):
        """Anything shipped that the Distributor hasn't confirmed yet."""
        return any(item.quantity_to_receive_remaining > 0 for item in self.items.all())

    @property
    def required_upfront_amount(self):
        pct = self.distributor_profile.upfront_payment_percentage
        return (self.grand_total * pct / Decimal("100")).quantize(Decimal("0.01"))

    @property
    def can_pay_now(self):
        """Whether the Distributor's order page offers a payment form.

        Before anything ships only an advance can be paid: when they said
        they'd pay one and none stands (e.g. the Owner rejected it), or
        when their terms require more up front than they've paid — the
        Owner won't ship until then. Answering "No advance" with no
        required advance means nothing is due until the goods arrive.
        After shipping, it's the remaining (final) payment."""
        if self.is_quote or self.status == self.Status.DECLINED:
            return False
        if self.pays_advance is None or self.remaining_amount <= 0:
            return False
        if self.status != self.Status.PENDING:
            return True
        if self.pays_advance and not self.advance_paid:
            return True
        return self.paid_so_far < self.required_upfront_amount

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

    list_price = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="The product's price before any discount, when ordered.",
    )

    discount_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0")),
            MaxValueValidator(Decimal("100")),
        ],
        help_text=(
            "Starts at the agreement's discount for this product; the "
            "Owner can change it until the line ships."
        ),
    )

    requested_unit_price = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text=(
            "The price the Distributor asked for on the Request to Quote "
            "(blank = asked the Owner to quote)."
        ),
    )

    unit_price = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text=(
            "The current agreed price — list_price less discount_percentage. "
            "Blank only on a Request to Quote."
        ),
    )

    owner_note = models.TextField(
        blank=True,
        help_text="The Owner's comment on this product, shown to the Distributor.",
    )

    unavailable = models.BooleanField(
        default=False,
        help_text=(
            "The Owner can't supply the rest of this line. Whatever hasn't "
            "shipped is dropped from the order (and its total), so the "
            "order can complete without it."
        ),
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
    def quantity_ordered(self):
        """What this line will actually supply — everything requested,
        unless the Owner marked it unavailable, then only what shipped."""
        if self.unavailable:
            return self.quantity_shipped
        return self.quantity_requested

    @property
    def quantity_dropped(self):
        return self.quantity_requested - self.quantity_ordered

    @property
    def quantity_to_ship_remaining(self):
        return self.quantity_ordered - self.quantity_shipped

    @property
    def quantity_to_receive_remaining(self):
        return self.quantity_shipped - self.quantity_received

    @property
    def is_complete(self):
        return self.quantity_received >= self.quantity_ordered

    @property
    def can_reprice(self):
        """Prices are settled once anything on the line has shipped."""
        return self.quantity_shipped == 0 and not self.unavailable

    @property
    def line_total(self):
        if self.unit_price is None:
            return Decimal("0.00")
        return (self.quantity_ordered * self.unit_price).quantize(Decimal("0.01"))

    @property
    def requested_price_given(self):
        """Whether the Distributor asked for a price. A 0 counts as not
        given — it means "please quote"."""
        return self.requested_unit_price not in (None, Decimal("0"))

    @property
    def price_highlight(self):
        """How the line is coloured once the Owner's price is in:
        "changed" (orange) — the Distributor asked for a price and the
        Owner's differs; "new" (yellow) — they left it empty and the
        Owner filled it in; "" (white) — it matches, or nothing is
        quoted yet. Orders placed directly, before the Request to Quote
        existed, have no asked-for price and are never highlighted."""
        if self.unit_price in (None, Decimal("0")) or not self.purchase_order.quoted_at:
            return ""
        if not self.requested_price_given:
            return "new"
        if self.unit_price != self.requested_unit_price:
            return "changed"
        return ""

    @property
    def price_changed(self):
        return bool(self.price_highlight)

    def __str__(self):
        return f"{self.product.sku} - {self.quantity_requested}"


class PurchaseOrderPayment(AuditedModel):
    Kind = PaymentKind

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

    kind = models.CharField(
        max_length=10,
        choices=PaymentKind.choices,
        default=PaymentKind.FINAL,
    )

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


class PurchaseOrderRevision(AuditedModel):
    """One step in the back-and-forth on a Distributor's order — their
    Request to Quote, each quote the Owner sends back, each counter-offer,
    every version of the Purchase Order and the Owner's invoice.

    Each one is a frozen copy of the lines as they stood at that moment
    plus the message that went with it, so the whole conversation can
    always be traced. Nothing here changes once it's saved; the live
    prices are still on PurchaseOrderItem."""

    class Stage(models.TextChoices):
        REQUEST = "REQUEST", "Request to Quote"
        QUOTE = "QUOTE", "Owner's quote"
        COUNTER = "COUNTER", "Distributor's counter-offer"
        PURCHASE_ORDER = "PURCHASE_ORDER", "Purchase Order"
        INVOICE = "INVOICE", "Invoice"

    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.PROTECT,
        related_name="revisions",
    )

    number = models.PositiveIntegerField()

    stage = models.CharField(max_length=20, choices=Stage.choices)

    by_owner = models.BooleanField(
        default=False,
        help_text="Said by the Owner (otherwise by the Distributor).",
    )

    message = models.TextField(blank=True)

    attachment = models.FileField(
        upload_to="purchase_order_revisions/",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["purchase_order", "number"]
        constraints = [
            models.UniqueConstraint(
                fields=["purchase_order", "number"],
                name="unique_revision_number_per_purchase_order",
            ),
        ]

    @property
    def total(self):
        total = sum(
            (line.line_total for line in self.lines.all() if not line.removed),
            Decimal("0.00"),
        )
        return total.quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.purchase_order.po_number} #{self.number} - {self.get_stage_display()}"


class PurchaseOrderRevisionLine(AuditedModel):
    revision = models.ForeignKey(
        PurchaseOrderRevision,
        on_delete=models.CASCADE,
        related_name="lines",
    )

    item = models.ForeignKey(
        PurchaseOrderItem,
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

    quantity = models.DecimalField(max_digits=18, decimal_places=4)

    unit_price = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Blank = no price given (please quote).",
    )

    removed = models.BooleanField(
        default=False,
        help_text="This line was dropped from the order at this step.",
    )

    class Meta:
        ordering = ["product__name"]

    @property
    def line_key(self):
        return self.product_id

    @property
    def line_total(self):
        if self.unit_price is None:
            return Decimal("0.00")
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.revision} - {self.product_id} - {self.unit_price}"
