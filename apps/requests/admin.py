from django.contrib import admin

from .models import (
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderPayment,
    PurchaseOrderRevision,
    PurchaseOrderRevisionLine,
)


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 0
    readonly_fields = (
        "product",
        "quantity_requested",
        "requested_unit_price",
        "unit_price",
        "quantity_shipped",
        "quantity_received",
    )

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PurchaseOrderRevisionInline(admin.TabularInline):
    model = PurchaseOrderRevision
    extra = 0
    fields = ("number", "stage", "by_owner", "message", "attachment", "created_by", "created_at")
    readonly_fields = fields

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
        "quoted_at",
        "po_placed_at",
        "invoice_number",
        "invoiced_at",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("po_number", "invoice_number", "distributor_profile__name")
    inlines = [PurchaseOrderItemInline, PurchaseOrderPaymentInline, PurchaseOrderRevisionInline]


class PurchaseOrderRevisionLineInline(admin.TabularInline):
    model = PurchaseOrderRevisionLine
    extra = 0
    fields = ("product", "quantity", "unit_price", "removed")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PurchaseOrderRevision)
class PurchaseOrderRevisionAdmin(admin.ModelAdmin):
    list_display = ("purchase_order", "number", "stage", "by_owner", "created_at")
    list_filter = ("stage", "by_owner")
    search_fields = ("purchase_order__po_number",)
    readonly_fields = ("purchase_order", "number", "stage", "by_owner", "message", "attachment")
    inlines = [PurchaseOrderRevisionLineInline]
