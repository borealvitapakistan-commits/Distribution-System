from django.contrib import admin

from .models import Batch


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "status",
        "manufacturer_order_item",
        "expiry_date",
        "received_at",
        "cancelled_at",
    )
    list_filter = ("status",)
    search_fields = (
        "code",
        "manufacturer_order_item__order__po_number",
        "manufacturer_order_item__product__sku",
        "manufacturer_order_item__product__name",
    )
    readonly_fields = [field.name for field in Batch._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
