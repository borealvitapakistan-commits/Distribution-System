from decimal import Decimal

from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from apps.owner_inventory.models import StockBalance
from apps.owner_warehouse.models import Location


def with_inventory(queryset):
    balances = StockBalance.objects.filter(
        product_id=OuterRef("pk"), location__location_type=Location.LocationType.OWN,
        location__is_sellable=True, location__active=True,
    ).order_by().values("product_id").annotate(total=Sum("quantity")).values("total")
    return queryset.annotate(
        inventory_available=Coalesce(
            Subquery(balances), Value(Decimal("0")),
            output_field=DecimalField(max_digits=18, decimal_places=4),
        ),
    )


def products_for_manufacturer(queryset, manufacturer):
    from .models import ProductPackagePrice

    links = ProductPackagePrice.objects.filter(manufacturer=manufacturer).values("product_id")
    return queryset.filter(Q(manufacturer=manufacturer) | Q(pk__in=Subquery(links)))


def unassigned_products(queryset):
    from .models import ProductPackagePrice

    links = ProductPackagePrice.objects.filter(manufacturer__archived_at__isnull=True).values("product_id")
    return queryset.filter(
        Q(manufacturer__isnull=True) | Q(manufacturer__archived_at__isnull=False),
    ).exclude(pk__in=Subquery(links))


def manufacturer_groups(queryset, manufacturers):
    from .models import ProductPackagePrice

    memberships = {manufacturer.pk:set() for manufacturer in manufacturers}
    products = dict(queryset.values_list("pk", "manufacturer_id"))
    assigned = set()
    for product_id, manufacturer_id in products.items():
        if manufacturer_id in memberships:
            memberships[manufacturer_id].add(product_id)
            assigned.add(product_id)
    for product_id, manufacturer_id in ProductPackagePrice.objects.filter(
        product_id__in=products, manufacturer_id__in=memberships,
    ).values_list("product_id", "manufacturer_id"):
        memberships[manufacturer_id].add(product_id)
        assigned.add(product_id)
    groups = [{"manufacturer":m, "key":str(m.pk), "count":len(memberships[m.pk])} for m in manufacturers]
    if set(products) - assigned:
        groups.append({"manufacturer":None, "key":"unassigned", "count":len(set(products) - assigned)})
    return groups


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
