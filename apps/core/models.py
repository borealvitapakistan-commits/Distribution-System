import uuid
from django.db import models
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
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
    """Effective singleton: standalone brand/business info for the one
    brand this system runs for. Other models may still reference it
    (e.g. Product, ManufacturerOrder) to record which brand a record
    belongs to."""

    name = models.CharField(max_length=200)
    legal_name = models.CharField(max_length=200, blank=True)
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

    logo = models.ImageField(
        upload_to="brand/logos/",
        null=True,
        blank=True
    )

    primary_color = models.CharField(
        max_length=7,
        default="#1f7a4d",
        validators=[
            RegexValidator(
                r"^#[0-9a-fA-F]{6}$",
                "Pick a colour or enter a hex code like #1f7a4d.",
            )
        ],
        help_text="Theme colour for this brand's Requests to Quote and Purchase Orders.",
    )

    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    active = models.BooleanField(default=True)

    objects = OwnerManagedQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def tinted_color(self, white_share):
        """The brand colour mixed with white — white_share 0 is the
        colour itself, 1 is pure white."""
        try:
            channels = [int(self.primary_color[i:i + 2], 16) for i in (1, 3, 5)]
        except (TypeError, ValueError):
            channels = [0x1F, 0x7A, 0x4D]

        mixed = [round(c + (255 - c) * white_share) for c in channels]
        return "#" + "".join(f"{c:02x}" for c in mixed)

    @property
    def primary_color_soft(self):
        return self.tinted_color(0.88)

    def __str__(self):
        return self.name
