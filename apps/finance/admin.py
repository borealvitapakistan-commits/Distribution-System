from django.contrib import admin

from .models import Expense, FinanceSettings, MarketResearch, SaleEntry


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("date", "name", "category", "amount")
    list_filter = ("category",)
    search_fields = ("name", "comments")


@admin.register(MarketResearch)
class MarketResearchAdmin(admin.ModelAdmin):
    list_display = ("product", "similar_product", "store", "pack_size", "market_price", "use_for_market_price")
    list_filter = ("use_for_market_price",)


@admin.register(SaleEntry)
class SaleEntryAdmin(admin.ModelAdmin):
    """Read-only: ledger entries are immutable."""

    list_display = ("occurred_at", "kind", "product", "batch_number", "quantity", "unit_cost", "net_unit_price")
    list_filter = ("kind", "cost_source")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(FinanceSettings)
