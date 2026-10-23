from decimal import Decimal

from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

from apps.owner_inventory.models import StockBalance
from apps.owner_warehouse.models import Location


def with_inventory(queryset):
    return queryset.annotate(
        inventory_available=Coalesce(
            Sum("stock_balances__quantity", filter=Q(
                stock_balances__location__location_type=Location.LocationType.OWN,
                stock_balances__location__is_sellable=True,
                stock_balances__location__active=True,
            )), Value(Decimal("0")), output_field=DecimalField(max_digits=18, decimal_places=4),
        ),
    )


def inventory_summary(product):
    balances = dict(StockBalance.objects.filter(product=product).values_list("location_id", "quantity"))
    locations = Location.objects.filter(on_book=True).filter(
        Q(active=True) | Q(pk__in=balances),
    ).order_by("name")
    rows = []
    totals = {key: Decimal("0") for key in ("unavailable", "in_transit", "available", "on_hand")}
    for location in locations:
        quantity = balances.get(location.pk, Decimal("0"))
        transit = quantity if location.location_type == Location.LocationType.TRANSIT else Decimal("0")
        on_hand = quantity - transit
        available = on_hand if (
            location.active and location.location_type == Location.LocationType.OWN and location.is_sellable
        ) else Decimal("0")
        row = {"location": location, "unavailable": on_hand - available,
               "in_transit": transit, "available": available, "on_hand": on_hand}
        for key in totals:
            totals[key] += row[key]
        rows.append(row)
    return {"rows": rows, "totals": totals}
