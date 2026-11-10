from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.templatetags.static import static
from django.urls import reverse

from .models import Brand

BRAND_CACHE_KEY = "core:brand"


def current_brand():
    """The single Brand record — kept in memory, since every page shows it
    and asking the database each time costs a round trip. Saving or
    deleting a Brand clears it (see below)."""
    brand = cache.get(BRAND_CACHE_KEY)
    if brand is None:
        brand = Brand.objects.first()
        if brand is not None:
            cache.set(BRAND_CACHE_KEY, brand, 300)
    return brand


@receiver(post_save, sender=Brand)
@receiver(post_delete, sender=Brand)
def _forget_brand(**kwargs):
    cache.delete(BRAND_CACHE_KEY)


def brand(request):
    """Makes the single Brand record available to every template as
    {{ brand }}, since no User points at it anymore."""
    current = current_brand()
    logo_url = (
        reverse("brand-logo", kwargs={"pk": current.pk})
        if current and current.logo
        else static("images/boreal-vita-logo.png")
    )
    return {"brand": current, "brand_logo_url": logo_url}
