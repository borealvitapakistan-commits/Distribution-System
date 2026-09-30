from django.contrib import admin

from .models import (
    DistributorStockBalance,
    DistributorStockBatch,
    DistributorStockMovement,
    SubDistributorSale,
)


@admin.register(DistributorStockMovement)
class DistributorStockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "distributor_profile",
        "product",
        "movement_type",
        "quantity",
        "from_location",
        "to_location",
        "created_by",
    )
    list_filter = ("movement_type",)
    search_fields = ("product__sku", "product__name", "reference", "distributor_profile__name")
    readonly_fields = [field.name for field in DistributorStockMovement._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DistributorStockBalance)
class DistributorStockBalanceAdmin(admin.ModelAdmin):
    list_display = ("distributor_profile", "product", "location", "quantity")
    list_filter = ("location",)
    search_fields = ("product__sku", "product__name", "location__code", "distributor_profile__name")
    readonly_fields = ("distributor_profile", "product", "location", "quantity")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DistributorStockBatch)
class DistributorStockBatchAdmin(admin.ModelAdmin):
    list_display = (
        "batch_number",
        "distributor_profile",
        "product",
        "location",
        "received_date",
        "expiry_date",
        "quantity_received",
        "quantity_remaining",
    )
    list_filter = ("location",)
    search_fields = ("batch_number", "product__sku", "product__name", "distributor_profile__name")
    readonly_fields = [field.name for field in DistributorStockBatch._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SubDistributorSale)
class SubDistributorSaleAdmin(admin.ModelAdmin):
    list_display = (
        "sale_date",
        "distributor_profile",
        "sub_distributor_name",
        "product",
        "quantity",
        "from_location",
        "batch",
        "created_at",
    )
    search_fields = (
        "sub_distributor_name",
        "product__sku",
        "product__name",
        "distributor_profile__name",
        "batch__batch_number",
    )
    readonly_fields = [field.name for field in SubDistributorSale._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
