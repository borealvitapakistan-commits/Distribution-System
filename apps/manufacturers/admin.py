from django.contrib import admin

from .models import Manufacturer


@admin.register(Manufacturer)
class ManufacturerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email", "active")
    list_filter = ("active",)
    search_fields = ("name", "email", "phone")
    ordering = ("name",)
