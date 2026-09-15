from django.contrib import admin

from .models import StockBalance, StockMovement


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "product",
        "movement_type",
        "quantity",
        "from_location",
        "to_location",
        "created_by",
    )
    list_filter = ("movement_type",)
    search_fields = ("product__sku", "product__name", "reference")
    readonly_fields = [field.name for field in StockMovement._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockBalance)
class StockBalanceAdmin(admin.ModelAdmin):
    list_display = ("product", "location", "quantity")
    list_filter = ("location",)
    search_fields = ("product__sku", "product__name", "location__code")
    readonly_fields = ("product", "location", "quantity")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
