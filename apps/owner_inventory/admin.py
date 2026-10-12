from django.contrib import admin

from .models import ShopifyOrder, ShopifyOrderLine, StockBalance, StockBatch, StockMovement


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "product",
        "movement_type",
        "channel",
        "quantity",
        "from_location",
        "to_location",
        "created_by",
    )
    list_filter = ("movement_type", "channel")
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


@admin.register(StockBatch)
class StockBatchAdmin(admin.ModelAdmin):
    list_display = (
        "batch_number",
        "product",
        "location",
        "received_date",
        "expiry_date",
        "quantity_received",
        "quantity_remaining",
    )
    list_filter = ("location",)
    search_fields = ("batch_number", "product__sku", "product__name")
    readonly_fields = [field.name for field in StockBatch._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ShopifyOrderLineInline(admin.TabularInline):
    model = ShopifyOrderLine
    extra = 0
    can_delete = False
    readonly_fields = [field.name for field in ShopifyOrderLine._meta.fields]


@admin.register(ShopifyOrder)
class ShopifyOrderAdmin(admin.ModelAdmin):
    list_display = ("name", "placed_at", "cancelled", "financial_status")
    search_fields = ("name", "shopify_order_id")
    readonly_fields = [field.name for field in ShopifyOrder._meta.fields]
    inlines = [ShopifyOrderLineInline]
