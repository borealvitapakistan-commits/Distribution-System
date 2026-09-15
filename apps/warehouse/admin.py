from django.contrib import admin

from .models import Location


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "location_type",
        "manufacturer",
        "distributor_profile",
        "customer",
        "is_sellable",
        "active",
    )
    list_filter = (
        "location_type",
        "is_sellable",
        "active",
    )
    search_fields = (
        "code",
        "name",
        "manufacturer__name",
        "distributor_profile__name",
        "customer__name",
    )
