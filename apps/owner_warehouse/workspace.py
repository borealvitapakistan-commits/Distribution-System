"""Read-only summaries for the inventory workspace."""
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.owner_inventory.models import StockBatch
from .models import Inventory, Location


def regions_for(user):
    return Inventory.objects.for_user(user).annotate(
        warehouse_count=Count("warehouses", filter=Q(warehouses__location_type=Location.LocationType.OWN), distinct=True),
        stored_units=Sum("warehouses__stock_balances__quantity",
                         filter=Q(warehouses__location_type=Location.LocationType.OWN), default=0),
        waiting_units=Sum("warehouses__stock_balances__quantity",
                          filter=Q(warehouses__location_type=Location.LocationType.UNALLOCATED), default=0),
    )


def warehouses_for(user):
    return Location.objects.for_user(user).filter(
        location_type=Location.LocationType.OWN,
    ).select_related("inventory").annotate(
        on_hand=Sum("stock_balances__quantity", default=0),
        product_count=Count("stock_balances__product", filter=Q(stock_balances__quantity__gt=0), distinct=True),
    ).order_by("inventory__name", "name")


def pending_for(user):
    return StockBatch.objects.for_user(user).available().filter(
        location__location_type=Location.LocationType.UNALLOCATED,
    ).select_related("product", "location", "location__inventory").fefo_ordered()


def selected(queryset, value):
    if not value:
        return None
    try:
        return queryset.filter(pk=value).first()
    except (ValidationError, ValueError):
        return None


def queue_context(request, queryset):
    summary = queryset.aggregate(queue_count=Count("pk"), queue_units=Sum("quantity_remaining", default=0))
    search = request.GET.get("q", "").strip()
    if search:
        queryset = queryset.filter(
            Q(product__name__icontains=search) | Q(product__sku__icontains=search)
            | Q(batch_number__icontains=search)
        )
    page = Paginator(queryset, 10).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return {**summary, "batch_page": page, "search": search, "page_query": query.urlencode(),
            "today": timezone.localdate()}
