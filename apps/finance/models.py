from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import AuditedModel, TimeStampedModel
from apps.core.querysets import OwnerManagedQuerySet
from apps.products.models import BottleSize


class FinanceSettings(TimeStampedModel):
    """One row: the assumptions the Product Finance sheet plans with."""

    distributor_cut_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("60.00"),
        validators=[
            MinValueValidator(Decimal("0")),
            MaxValueValidator(Decimal("100")),
        ],
        help_text=(
            "The share of the retail price a Distributor keeps — e.g. 60 "
            "means we receive Rs 480 of a Rs 1,200 retail price."
        ),
    )

    @classmethod
    def load(cls):
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings

    def __str__(self):
        return f"Finance settings (distributor cut {self.distributor_cut_percentage}%)"


class Expense(AuditedModel):
    """A business cost that isn't the stock itself — rent, salaries,
    marketing, courier charges. The total for a period is shared across
    products on the Product Finance sheet and subtracted from gross
    profit on the reports."""

    class Category(models.TextChoices):
        MARKETING = "MARKETING", "Marketing"
        SALARIES = "SALARIES", "Salaries"
        RENT = "RENT", "Rent"
        UTILITIES = "UTILITIES", "Utilities"
        FREIGHT = "FREIGHT", "Freight & courier"
        PACKAGING = "PACKAGING", "Packaging & labels"
        REGULATORY = "REGULATORY", "Regulatory & licence"
        BANK_FEES = "BANK_FEES", "Bank & gateway fees"
        PROFESSIONAL = "PROFESSIONAL", "Professional fees"
        TRAVEL = "TRAVEL", "Travel"
        OTHER = "OTHER", "Other"

    date = models.DateField(default=date.today)
    name = models.CharField(max_length=200)
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.OTHER,
    )
    amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    comments = models.TextField(blank=True)

    objects = OwnerManagedQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["date"])]

    def clean(self):
        super().clean()

        self.name = self.name.strip()

        if not self.name:
            raise ValidationError({"name": "Expense name is required."})

    def __str__(self):
        return f"{self.date} - {self.name} - {self.amount}"


class MarketResearch(AuditedModel):
    """What a similar product sells for on another store. Rows marked
    use_for_market_price set the Market Price shown against our own
    product on the Product Finance and Product Prices sheets."""

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="market_research",
    )
    similar_product = models.CharField(max_length=255)
    store = models.CharField(max_length=150, blank=True)
    pack_size = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="How much is in their pack, e.g. 30 (capsules) or 300 (grams).",
    )
    unit = models.CharField(
        max_length=30,
        default="capsules",
        help_text="capsules, tablets, g, ml…",
    )
    market_price = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Their regular price for the whole pack.",
    )
    use_for_market_price = models.BooleanField(
        default=True,
        help_text="Count this row when working out our product's market price.",
    )
    checked_on = models.DateField(default=date.today)
    note = models.CharField(max_length=255, blank=True)
    link = models.URLField(max_length=500, blank=True)

    objects = OwnerManagedQuerySet.as_manager()

    class Meta:
        ordering = ["product__name", "-use_for_market_price", "store"]
        verbose_name_plural = "market research"

    @property
    def price_per_unit(self):
        return self.market_price / self.pack_size

    def __str__(self):
        return f"{self.product.name} vs {self.similar_product} ({self.store})"


class SaleEntryQuerySet(OwnerManagedQuerySet):
    def between(self, start, end):
        """Entries dated start..end inclusive."""
        return self.filter(occurred_on__gte=start, occurred_on__lte=end)


class SaleEntry(AuditedModel):
    """Immutable finance ledger row: one stock movement that took stock
    out of the Owner's hands, with everything money-related frozen at
    that moment — what one unit cost us (from its batch), what it retails
    for, and what we actually received for it.

    Created inside the same transaction as the movement, and never edited
    afterwards, so raising a price or re-editing a Manufacturer invoice
    later can't silently rewrite past profit."""

    class Kind(models.TextChoices):
        DISTRIBUTOR_SALE = "DISTRIBUTOR_SALE", "Distributor sale"
        SHOPIFY_SALE = "SHOPIFY_SALE", "Shopify sale"
        GIFT = "GIFT", "Given free to Distributor"
        WRITE_OFF = "WRITE_OFF", "Stock written off"

    SALE_KINDS = (Kind.DISTRIBUTOR_SALE, Kind.SHOPIFY_SALE)

    class CostSource(models.TextChoices):
        BATCH = "BATCH", "Batch (Manufacturer invoice)"
        PRICE_LIST = "PRICE_LIST", "Product price list"
        UNKNOWN = "UNKNOWN", "Unknown"

    movement = models.OneToOneField(
        "owner_inventory.StockMovement",
        on_delete=models.PROTECT,
        related_name="sale_entry",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    occurred_at = models.DateTimeField()
    occurred_on = models.DateField(
        help_text="occurred_at as a local (Pakistan) date — what reports group by.",
    )

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="sale_entries",
    )
    bottle_size = models.PositiveSmallIntegerField(
        choices=BottleSize.choices,
        null=True,
        blank=True,
    )
    stock_batch = models.ForeignKey(
        "owner_inventory.StockBatch",
        on_delete=models.PROTECT,
        related_name="sale_entries",
        null=True,
        blank=True,
    )
    source_batch = models.ForeignKey(
        "batches.Batch",
        on_delete=models.PROTECT,
        related_name="sale_entries",
        null=True,
        blank=True,
        help_text="The Manufacturer order lot the stock came from.",
    )
    batch_number = models.CharField(max_length=100, blank=True)

    distributor_profile = models.ForeignKey(
        "distributors.DistributorProfile",
        on_delete=models.PROTECT,
        related_name="sale_entries",
        null=True,
        blank=True,
    )
    purchase_order_item = models.ForeignKey(
        "requests.PurchaseOrderItem",
        on_delete=models.PROTECT,
        related_name="sale_entries",
        null=True,
        blank=True,
    )
    shopify_order_line = models.ForeignKey(
        "owner_inventory.ShopifyOrderLine",
        on_delete=models.PROTECT,
        related_name="sale_entries",
        null=True,
        blank=True,
    )

    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_cost = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        help_text="Landed cost of one unit: Manufacturer price plus its share of tax and shipping.",
    )
    cost_source = models.CharField(max_length=20, choices=CostSource.choices)
    retail_unit_price = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="What one unit retails for.",
    )
    net_unit_price = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="What we actually received for one unit (0 for free stock).",
    )
    reference = models.CharField(max_length=255, blank=True)

    objects = SaleEntryQuerySet.as_manager()

    class Meta:
        ordering = ["-occurred_at"]
        verbose_name_plural = "sale entries"
        indexes = [
            models.Index(fields=["occurred_on", "kind"]),
            models.Index(fields=["product", "occurred_on"]),
        ]

    @property
    def is_sale(self):
        return self.kind in self.SALE_KINDS

    @property
    def retail_value(self):
        return self.quantity * self.retail_unit_price

    @property
    def revenue(self):
        return self.quantity * self.net_unit_price

    @property
    def cut(self):
        """Distributor cut (or Shopify discount): retail value we didn't receive."""
        return self.retail_value - self.revenue

    @property
    def cost(self):
        return self.quantity * self.unit_cost

    @property
    def profit(self):
        return self.revenue - self.cost

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Finance ledger entries are immutable.")

        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Finance ledger entries cannot be deleted.")

    def __str__(self):
        return f"{self.get_kind_display()} - {self.product.sku} x {self.quantity}"
