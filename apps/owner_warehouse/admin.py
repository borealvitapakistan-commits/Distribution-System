from django.contrib import admin

from .models import Inventory, Location


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "active")
    list_filter = ("active",)
    search_fields = ("code", "name")


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "location_type",
        "inventory",
        "manufacturer",
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
        "inventory__name",
        "manufacturer__name",
        "customer__name",
    )
