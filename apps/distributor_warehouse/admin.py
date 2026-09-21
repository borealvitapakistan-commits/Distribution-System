from django.contrib import admin

from .models import DistributorInventory, DistributorLocation


@admin.register(DistributorInventory)
class DistributorInventoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "distributor_profile", "active")
    list_filter = ("active",)
    search_fields = ("code", "name", "distributor_profile__name")


@admin.register(DistributorLocation)
class DistributorLocationAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "location_type",
        "distributor_profile",
        "distributor_inventory",
        "active",
    )
    list_filter = ("location_type", "active")
    search_fields = (
        "code",
        "name",
        "distributor_profile__name",
        "distributor_inventory__name",
    )
