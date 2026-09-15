from django.contrib import admin

from .models import OwnerProfile


@admin.register(OwnerProfile)
class OwnerProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "active")
    list_filter = ("active",)
    search_fields = ("name", "user__email")
