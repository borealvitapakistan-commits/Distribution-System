from django.contrib import admin

from .models import Brand


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "legal_name", "base_currency", "timezone", "active")
    list_filter = ("active", "base_currency")
    search_fields = ("name", "legal_name", "ntn", "strn", "enlistment_number")
