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
        blank=True,
        help_text="Only the file name is kept here; the image itself is a BrandLogo row.",
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

    @property
    def logo_url(self):
        from django.urls import reverse

        return reverse("brand-logo", kwargs={"pk": self.pk}) if self.logo else ""

    def save(self, *args, **kwargs):
        from django.db import transaction

        new_logo = None
        if self.logo and not self.logo._committed:
            # A new upload: keep its bytes in BrandLogo instead of a file.
            import mimetypes
            import posixpath

            upload = self.logo.file
            upload.seek(0)
            new_logo = {
                "data": upload.read(),
                "content_type": (
                    getattr(upload, "content_type", "")
                    or mimetypes.guess_type(self.logo.name)[0]
                    or "application/octet-stream"
                ),
            }
            self.logo.name = posixpath.join("brand/logos", posixpath.basename(self.logo.name))
            self.logo._committed = True

        with transaction.atomic():
            super().save(*args, **kwargs)
            if not self.logo:
                BrandLogo.objects.filter(brand=self).delete()
            elif new_logo:
                BrandLogo.objects.update_or_create(brand=self, defaults=new_logo)

    def __str__(self):
        return self.name


class BrandLogo(models.Model):
    """The brand logo's image, kept in the database so every server (and
    every page, PDF and sign-in screen) can show it without a shared disk
    or bucket. Its own table, so loading a Brand — which nearly every
    page does, often joined to every order — never drags the image along.
    Served by the brand-logo view."""

    brand = models.OneToOneField(
        Brand, on_delete=models.CASCADE, primary_key=True, related_name="logo_image",
    )
    data = models.BinaryField()
    content_type = models.CharField(max_length=100)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Logo of {self.brand_id}"


class DataVersion(models.Model):
    """One row whose number goes up whenever anything is changed, so a
    page saved in memory is only shown again while it's still current
    (see apps.core.page_cache)."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    number = models.BigIntegerField(default=0)

    def __str__(self):
        return f"Data version {self.number}"
