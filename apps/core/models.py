import uuid
from django.db import models
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from decimal import Decimal
from apps.core.querysets import OwnerManagedQuerySet


class UUIDModel(models.Model):
    """Provides UUID a Primary Key"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    class Meta:
        abstract = True



class TimeStampedModel(models.Model):
    """Track Record creation and modification time"""

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True


class AuditedModel(UUIDModel, TimeStampedModel):
    """Tracks which user created and updated a record."""

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        abstract = True



class Brand(UUIDModel, TimeStampedModel):
    """Standalone brand/business info. Not linked to any other model —
    this system runs for a single brand, so there's no scoping to do."""

    name = models.CharField(max_length=200)
    legal_name = models.CharField(max_length=200, blank=True)
    enlistment_number = models.CharField(
        max_length=100,
        blank=True,
    )
    ntn = models.CharField(max_length=50, blank=True)
    strn = models.CharField(
        max_length=50,
        blank=True,
    )
    base_currency = models.CharField(max_length=3, default="PKR")
    timezone = models.CharField(max_length=50, default="Asia/Karachi")
    fiscal_year_start_month = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(12)]
    )
    default_country = models.CharField(
        max_length=2,
        default="PK",
    )
    default_low_stock_threshold = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[MinValueValidator(Decimal("0"))]
    )

    logo = models.ImageField(
        upload_to="brand/logos/",
        null=True,
        blank=True
    )

    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    active = models.BooleanField(default=True)

    objects = OwnerManagedQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
