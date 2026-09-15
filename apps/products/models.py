from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.text import slugify

from apps.core.models import AuditedModel

from .querysets import (
    OwnerOnlyQuerySet,
    ProductIngredientQuerySet,
    ProductQuerySet,
)


class ProductCategory(AuditedModel):
    name = models.CharField(max_length=150)
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
    )
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    objects = OwnerOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "name"],
                name="unique_category_name_per_parent",
            ),
        ]

    def clean(self):
        super().clean()

        self.name = self.name.strip()

        if not self.name:
            raise ValidationError(
                {"name": "Category name is required."}
            )

        if self.parent_id == self.pk:
            raise ValidationError(
                {"parent": "A category cannot be its own parent."}
            )

    def __str__(self):
        return self.name


class Product(AuditedModel):
    class UnitOfMeasure(models.TextChoices):
        PIECE = "PIECE", "Piece"
        BOTTLE = "BOTTLE", "Bottle"
        BOX = "BOX", "Box"
        CASE = "CASE", "Case"
        KG = "KG", "Kilogram"
        GRAM = "GRAM", "Gram"
        LITRE = "LITRE", "Litre"
        ML = "ML", "Millilitre"
        OTHER = "OTHER", "Other"

    class StrengthBasis(models.TextChoices):
        PER_CAPSULE = "PER_CAPSULE", "Per capsule"
        PER_SERVING = "PER_SERVING", "Per serving"
        PER_TABLET = "PER_TABLET", "Per tablet"
        PER_ML = "PER_ML", "Per ml"
        OTHER = "OTHER", "Other"

    DISCRETE_UNITS = {
        UnitOfMeasure.PIECE,
        UnitOfMeasure.BOTTLE,
        UnitOfMeasure.BOX,
        UnitOfMeasure.CASE,
    }

    category = models.ForeignKey(
        ProductCategory,
        on_delete=models.PROTECT,
        related_name="products",
        null=True,
        blank=True,
    )
    manufacturer = models.ForeignKey(
        "manufacturers.Manufacturer",
        on_delete=models.PROTECT,
        related_name="products",
        null=True,
        blank=True,
    )
    brand = models.ForeignKey(
        "core.Brand",
        on_delete=models.PROTECT,
        related_name="products",
        null=True,
        blank=True,
    )
    sku = models.CharField(max_length=80, unique=True)
    barcode = models.CharField(
        max_length=100,
        blank=True,
    )
    handle = models.SlugField(
        max_length=200,
        unique=True,
        blank=True,
        help_text="Used as the Shopify product handle. Auto-filled from the name if left blank.",
    )
    name = models.CharField(max_length=200)
    generic_name = models.CharField(
        max_length=200,
        blank=True,
    )
    brand_name = models.CharField(
        max_length=200,
        blank=True,
    )
    indications = models.TextField(blank=True)
    body_html = models.TextField(
        blank=True,
        help_text="Shopify product description (HTML allowed).",
    )
    tags = models.CharField(
        max_length=255,
        blank=True,
        help_text="Comma-separated, e.g. \"vitamins, immunity\".",
    )
    enlistment_number = models.CharField(
        max_length=100,
        blank=True,
    )
    form = models.CharField(
        max_length=100,
        blank=True,
    )
    pack_size = models.CharField(
        max_length=100,
        blank=True,
    )
    unit_of_measure = models.CharField(
        max_length=12,
        choices=UnitOfMeasure.choices,
        default=UnitOfMeasure.PIECE,
    )
    units_per_case = models.PositiveIntegerField(default=1)
    serving_size = models.CharField(
        max_length=100,
        blank=True,
    )
    strength_basis = models.CharField(
        max_length=20,
        choices=StrengthBasis.choices,
        blank=True,
    )
    base_retail_price = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0"))
        ],
    )
    compare_at_price = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(Decimal("0"))
        ],
        help_text="Shown as the \"was\" price on Shopify. Leave blank if not discounted.",
    )
    weight_grams = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0"))
        ],
    )
    image = models.ImageField(
        upload_to="products/",
        null=True,
        blank=True,
    )
    currency = models.CharField(
        max_length=3,
        default="PKR",
    )
    shelf_life_months = models.PositiveIntegerField(
        default=0
    )
    hs_code = models.CharField(
        max_length=30,
        blank=True,
    )
    default_reorder_point = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[
            MinValueValidator(Decimal("0"))
        ],
    )
    default_reorder_quantity = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[
            MinValueValidator(Decimal("0"))
        ],
    )
    allow_fractional_quantity = models.BooleanField(
        default=False
    )
    active = models.BooleanField(default=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["name", "sku"]
        constraints = [
            models.UniqueConstraint(
                fields=["barcode"],
                condition=~Q(barcode=""),
                name="unique_product_barcode",
            ),
        ]

    def clean(self):
        super().clean()

        self.sku = self.sku.strip().upper()
        self.barcode = self.barcode.strip()
        self.name = self.name.strip()
        self.currency = self.currency.strip().upper()
        self.tags = self.tags.strip()

        if not self.sku:
            raise ValidationError(
                {"sku": "SKU is required."}
            )

        if not self.name:
            raise ValidationError(
                {"name": "Product name is required."}
            )

        if not self.handle:
            self.handle = slugify(self.name) or slugify(self.sku)

        if len(self.currency) != 3:
            raise ValidationError(
                {
                    "currency": (
                        "Currency must be a 3-letter code."
                    )
                }
            )

        if self.units_per_case < 1:
            raise ValidationError(
                {
                    "units_per_case": (
                        "Units per case must be at least 1."
                    )
                }
            )

        if (
            self.unit_of_measure in self.DISCRETE_UNITS
            and self.allow_fractional_quantity
        ):
            raise ValidationError(
                {
                    "allow_fractional_quantity": (
                        "Piece, bottle, box and case "
                        "products must use whole quantities."
                    )
                }
            )

    def __str__(self):
        return f"{self.sku} - {self.name}"


class Ingredient(AuditedModel):
    name = models.CharField(max_length=200, unique=True)
    botanical_name = models.CharField( max_length=200, blank=True)
    part_used = models.CharField(max_length=100, blank=True)
    default_unit = models.CharField(max_length=30, blank=True)
    active = models.BooleanField(default=True)
    objects = OwnerOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def clean(self):
        super().clean()

        self.name = self.name.strip()

        if not self.name:
            raise ValidationError(
                {"name": "Ingredient name is required."}
            )

    def __str__(self):
        return self.name


class ProductIngredient(AuditedModel):
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="ingredients",
    )
    ingredient = models.ForeignKey(
        Ingredient,
        on_delete=models.PROTECT,
        related_name="product_links",
    )
    strength = models.CharField(max_length=100, blank=True)
    unit = models.CharField(max_length=30, blank=True)
    is_medicinal = models.BooleanField(default=False)
    extract_ratio = models.CharField(max_length=100, blank=True)
    equivalent_to = models.CharField(max_length=100, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    objects = ProductIngredientQuerySet.as_manager()

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "ingredient"],
                name="unique_product_ingredient",
            ),
        ]

    def __str__(self):
        return (
            f"{self.product.sku} - "
            f"{self.ingredient.name}"
        )
