from django.contrib import admin

from .models import (
    Manufacturer,
    ManufacturerOrder,
    ManufacturerOrderItem,
    ManufacturerOrderPayment,
)


@admin.register(Manufacturer)
class ManufacturerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email", "upfront_payment_percentage", "active")
    list_filter = ("active",)
    search_fields = ("name", "email", "phone")
    ordering = ("name",)


class ManufacturerOrderItemInline(admin.TabularInline):
    model = ManufacturerOrderItem
    extra = 0
    readonly_fields = ("product", "quantity", "unit_price", "source_purchase_order_item")

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ManufacturerOrderPaymentInline(admin.TabularInline):
    model = ManufacturerOrderPayment
    extra = 0
    readonly_fields = ("amount", "paid_at", "note")

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ManufacturerOrder)
class ManufacturerOrderAdmin(admin.ModelAdmin):
    list_display = ("po_number", "manufacturer", "brand", "status", "received_at", "created_at")
    list_filter = ("status",)
    search_fields = ("po_number", "manufacturer__name", "brand__name")
    inlines = [ManufacturerOrderItemInline, ManufacturerOrderPaymentInline]
