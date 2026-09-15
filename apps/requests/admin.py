from django.contrib import admin

from .models import StockRequest, StockRequestItem


class StockRequestItemInline(admin.TabularInline):
    model = StockRequestItem
    extra = 0
    readonly_fields = ("product", "quantity_requested", "quantity_fulfilled")

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockRequest)
class StockRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "distributor_profile",
        "status",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("distributor_profile__name",)
    inlines = [StockRequestItemInline]
