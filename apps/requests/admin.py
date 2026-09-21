from django.contrib import admin

from .models import PurchaseOrder, PurchaseOrderItem, PurchaseOrderPayment


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 0
    readonly_fields = (
        "product",
        "quantity_requested",
        "unit_price",
        "quantity_shipped",
        "quantity_received",
    )

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PurchaseOrderPaymentInline(admin.TabularInline):
    model = PurchaseOrderPayment
    extra = 0
    readonly_fields = ("amount", "paid_at", "proof", "note")

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = (
        "po_number",
        "distributor_profile",
        "status",
        "invoiced_at",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("po_number", "distributor_profile__name")
    inlines = [PurchaseOrderItemInline, PurchaseOrderPaymentInline]
