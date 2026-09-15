from django.contrib import admin

from .models import DistributorProfile


@admin.register(DistributorProfile)
class DistributorProfileAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user",
        "commission_percentage",
        "approval_status",
        "approved_by",
        "approved_at",
    )
    list_filter = ("approval_status",)
    search_fields = ("name", "user__email")
    readonly_fields = ("approved_by", "approved_at")
