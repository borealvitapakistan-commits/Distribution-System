"""Keeps Shopify's stock count equal to this system's.

There is one inventory: the Owner's own warehouses. Shopify doesn't get
a warehouse of its own — it sells from the same stock a Distributor is
shipped from, and the quantity it shows for a product is the total in
every active, sellable owned warehouse. Stock still unallocated or in
Transit isn't counted: it can't be picked for an online order yet.

A sync is two steps, always in this order:

1. Pull — every new Shopify order is recorded here as a ShopifyOrder,
   and each line takes its quantity out of stock (FEFO, from sellable
   owned warehouses) as a SALE movement on the Shopify channel. That's
   what makes a Shopify sale traceable to a batch and warehouse.
   Then any change made by hand on Shopify (its Inventory screen) since
   the last sync is recorded here as an ADJUSTMENT on the Shopify
   channel: a decrease is taken out of batches FEFO, an increase lands
   as a new lot in the warehouse that last received the product.
2. Publish — every active product with sellable stock that isn't on
   Shopify yet is created there (title, description, price, SKU, barcode,
   weight, image, type, tags) and put on the Online Store. A Shopify
   product with the same SKU or handle is linked instead, so nothing is
   created twice.
3. Push — Shopify's "available" count for each linked product is set to
   this system's sellable total.

Pulling first means an online sale Shopify already counted is never
overwritten by a stale total. Every stock movement queues a sync once
its transaction commits (when SHOPIFY_INVENTORY_SYNC is on), and the
sync_shopify management command / "Sync now" button run it on demand.
"""

import logging
import mimetypes
import os
import threading
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Max, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.audit.services import record_audit_event
from apps.products import shopify

from .models import (
    ShopifyOrder,
    ShopifyOrderLine,
    ShopifySyncState,
    StockBalance,
    StockMovement,
)

logger = logging.getLogger(__name__)

_local = threading.local()

# Orders are re-fetched from a little before the newest one already
# imported, so one placed while the last sync ran isn't missed. Orders
# already imported are skipped by their Shopify ID.
ORDER_OVERLAP = timedelta(hours=1)


def _price(amount):
    return Decimal(str(amount)).quantize(Decimal("0.01")) if amount not in (None, "") else None


def is_enabled():
    return settings.SHOPIFY_INVENTORY_SYNC and shopify.is_configured()


def sellable_quantities(products=None):
    """{product id: quantity} in every active, sellable owned warehouse —
    the number Shopify shows."""
    from apps.owner_warehouse.models import Location

    balances = StockBalance.objects.filter(
        location__location_type=Location.LocationType.OWN,
        location__is_sellable=True,
        location__active=True,
    )

    if products is not None:
        balances = balances.filter(product__in=products)

    return {
        row["product"]: row["total"] or Decimal("0")
        for row in balances.values("product").annotate(total=Sum("quantity"))
    }


def schedule_push():
    """Queue one sync for when the current transaction commits. Called by
    post_stock_movement, so every stock change reaches Shopify; repeated
    calls in one transaction still sync once."""
    if not is_enabled() or getattr(_local, "syncing", False):
        return

    pending = getattr(connection, "run_on_commit", None) or []
    if any(callback is _sync_after_commit for _sids, callback, *_ in pending):
        return

    transaction.on_commit(_sync_after_commit)


def _sync_after_commit():
    """A Shopify outage must never undo or block a stock change that's
    already saved — the error is kept for the sync page, and the next
    sync catches Shopify up."""
    try:
        sync()
    except Exception as exc:
        logger.exception("Shopify inventory sync failed")
        _record_error(exc)


def _record_error(exc):
    state = ShopifySyncState.load()
    state.last_error = str(exc)[:2000]
    state.save(update_fields=["last_error", "updated_at"])


def _system_owner():
    from apps.accounts.models import User

    owner = (
        User.objects
        .filter(role=User.Role.OWNER, is_active=True)
        .order_by("pk")
        .first()
    )
    if owner is None:
        raise shopify.ShopifyError("No active Owner account to record Shopify sales under.")
    return owner


def sync(*, actor=None):
    """Pull new Shopify orders into stock, then push every linked
    product's sellable total to Shopify. Returns a summary dict."""
    if not shopify.is_configured():
        raise shopify.ShopifyError(
            "Shopify isn't connected. Add SHOPIFY_STORE_DOMAIN, "
            "SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET to the .env file."
        )

    actor = actor or _system_owner()
    _local.syncing = True

    try:
        scopes = shopify.require_scopes(*shopify.INVENTORY_SYNC_SCOPES)

        state = ShopifySyncState.load()
        state.location_id, state.location_name = shopify.pick_location(
            settings.SHOPIFY_LOCATION_ID
        )

        summary = {"orders_imported": 0, "lines_posted": 0, "lines_short": 0,
                   "lines_unlinked": 0, "products_created": 0, "products_linked": 0,
                   "products_pushed": 0, "manual_changes": [], "warnings": []}

        # Shopify's numbers before any order is pulled: an order placed after
        # this moment is left for the next sync on both sides of the sum.
        snapshot_at = timezone.now()
        snapshot = _shopify_snapshot(state)

        if state.orders_since is None:
            # First sync: Shopify's past orders were sold before this
            # system's stock was counted — importing them would take the
            # same bottles out twice.
            state.orders_since = timezone.now()
        else:
            pull_orders(actor=actor, state=state, summary=summary)

        retry_open_lines(actor=actor, summary=summary)
        pull_manual_changes(
            actor=actor, state=state, snapshot=snapshot, snapshot_at=snapshot_at, summary=summary
        )
        state.last_pulled_at = timezone.now()

        publish_missing_products(actor=actor, state=state, scopes=scopes, summary=summary)
        pushed_at = timezone.now()
        push_quantities(actor=actor, state=state, summary=summary)
        state.last_pushed_at = pushed_at
        state.last_error = ""
        state.save()

        return summary
    except Exception as exc:
        _record_error(exc)
        raise
    finally:
        _local.syncing = False


def pull_orders(*, actor, state, summary):
    newest = ShopifyOrder.objects.aggregate(newest=Max("placed_at"))["newest"]
    since = max(state.orders_since, newest - ORDER_OVERLAP) if newest else state.orders_since

    known = set(ShopifyOrder.objects.values_list("shopify_order_id", flat=True))

    for data in shopify.fetch_orders(since):
        if data["id"] in known:
            continue

        placed_at = parse_datetime(data["created_at"])
        if placed_at < state.orders_since:
            continue

        import_order(actor=actor, data=data, summary=summary)
        summary["orders_imported"] += 1


@transaction.atomic
def import_order(*, actor, data, summary):
    from apps.products.models import Product

    order = ShopifyOrder.objects.create(
        shopify_order_id=data["id"],
        name=data["name"],
        placed_at=parse_datetime(data["created_at"]),
        cancelled=data["cancelled"],
        financial_status=data["financial_status"],
    )

    for line_data in data["lines"]:
        product = (
            Product.objects.filter(shopify_product_id=line_data["product_id"]).first()
            if line_data["product_id"] else None
        )
        line = ShopifyOrderLine.objects.create(
            order=order,
            shopify_line_id=line_data["id"],
            shopify_product_id=line_data["product_id"],
            title=line_data["title"][:255],
            product=product,
            quantity=Decimal(line_data["quantity"]),
            original_unit_price=_price(line_data.get("original_unit_price")),
            unit_price=_price(line_data.get("unit_price")),
            status=(
                ShopifyOrderLine.Status.SKIPPED if order.cancelled
                else ShopifyOrderLine.Status.UNLINKED
            ),
        )

        if not order.cancelled:
            post_line(actor=actor, line=line, summary=summary)

    record_audit_event(
        user=actor,
        action="inventory.shopify_order_imported",
        instance=order,
        after_data={
            "shopify_order_id": order.shopify_order_id,
            "cancelled": order.cancelled,
            "lines": [
                {
                    "title": line.title,
                    "product_id": str(line.product_id) if line.product_id else None,
                    "quantity": str(line.quantity),
                    "status": line.status,
                }
                for line in order.lines.all()
            ],
        },
    )

    return order


def retry_open_lines(*, actor, summary):
    """Lines that couldn't take stock last time — the product wasn't
    linked yet, or there wasn't enough — get another go every sync."""
    from apps.products.models import Product

    for line in (
        ShopifyOrderLine.objects
        .filter(status__in=[ShopifyOrderLine.Status.SHORT, ShopifyOrderLine.Status.UNLINKED])
        .select_related("order")
        .order_by("order__placed_at")
    ):
        if line.product_id is None and line.shopify_product_id:
            line.product = Product.objects.filter(
                shopify_product_id=line.shopify_product_id
            ).first()

        with transaction.atomic():
            post_line(actor=actor, line=line, summary=summary)


def post_line(*, actor, line, summary):
    """Takes one Shopify order line out of stock — all of it, or none,
    so a line is never half-posted."""
    from apps.finance.services import record_sale

    from .services import post_stock_movement, shippable_batches_fefo

    if line.product_id is None:
        line.status = ShopifyOrderLine.Status.UNLINKED
        line.note = "No product here is linked to this Shopify product."
        line.save(update_fields=["product", "status", "note", "updated_at"])
        summary["lines_unlinked"] += 1
        return

    batches = list(
        shippable_batches_fefo(product=line.product)
        .filter(location__active=True)
        .select_related("location")
        .select_for_update(of=("self",))
    )
    on_hand = sum((batch.quantity_remaining for batch in batches), Decimal("0"))

    if on_hand < line.quantity:
        line.status = ShopifyOrderLine.Status.SHORT
        line.note = f"Only {on_hand.normalize():f} in sellable warehouses."
        line.save(update_fields=["product", "status", "note", "updated_at"])
        summary["lines_short"] += 1
        return

    remaining = line.quantity
    for batch in batches:
        if remaining <= 0:
            break

        take = min(remaining, batch.quantity_remaining)
        movement = post_stock_movement(
            actor=actor,
            product=line.product,
            quantity=take,
            movement_type=StockMovement.MovementType.SALE,
            channel=StockMovement.Channel.SHOPIFY,
            from_location=batch.location,
            to_location=None,
            reference=f"Shopify order {line.order.name}",
            batch=batch,
            shopify_order=line.order,
        )
        record_sale(actor=actor, movement=movement, shopify_order_line=line)
        remaining -= take

    line.status = ShopifyOrderLine.Status.POSTED
    line.note = ""
    line.save(update_fields=["product", "status", "note", "updated_at"])
    summary["lines_posted"] += 1


def _shopify_snapshot(state):
    """{product id: Shopify available} for every product Shopify was last
    told a number for — the baseline manual changes are measured from."""
    from apps.products.models import Product

    if not state.location_id or state.last_pushed_at is None:
        return {}

    products = list(
        Product.objects
        .exclude(shopify_inventory_item_id="")
        .filter(shopify_pushed_quantity__isnull=False)
    )
    live = shopify.fetch_available(
        [product.shopify_inventory_item_id for product in products], state.location_id
    )
    return {
        product.pk: live[product.shopify_inventory_item_id]
        for product in products
        if live.get(product.shopify_inventory_item_id) is not None
    }


def _sold_on_shopify_between(product, start, end):
    """Units of this product Shopify took off its own count for orders
    placed in (start, end]. Cancelled orders are taken as restocked."""
    total = (
        ShopifyOrderLine.objects
        .filter(
            shopify_product_id=product.shopify_product_id,
            order__cancelled=False,
            order__placed_at__gt=start,
            order__placed_at__lte=end,
        )
        .aggregate(total=Sum("quantity"))["total"]
    )
    return int(total or 0)


def take_from_stock(*, actor, product, quantity, movement_type, reference, shopify_order=None):
    """Draws quantity out of sellable warehouses, soonest-expiry batch
    first, as one movement per batch. Takes what there is when there
    isn't enough; returns how much it took."""
    from apps.finance.services import record_sale

    from .services import post_stock_movement, shippable_batches_fefo

    batches = (
        shippable_batches_fefo(product=product)
        .filter(location__active=True)
        .select_related("location")
        .select_for_update(of=("self",))
    )
    remaining = Decimal(quantity)
    for batch in batches:
        if remaining <= 0:
            break
        take = min(remaining, batch.quantity_remaining)
        movement = post_stock_movement(
            actor=actor,
            product=product,
            quantity=take,
            movement_type=movement_type,
            channel=StockMovement.Channel.SHOPIFY,
            from_location=batch.location,
            to_location=None,
            reference=reference,
            batch=batch,
            shopify_order=shopify_order,
        )
        record_sale(actor=actor, movement=movement)
        remaining -= take
    return Decimal(quantity) - remaining


def _warehouse_for_increase(product):
    """Where stock added on Shopify lands: the sellable warehouse that
    last received this product, else the first sellable warehouse."""
    from apps.owner_warehouse.models import Location

    from .models import StockBatch

    latest = (
        StockBatch.objects
        .filter(
            product=product,
            location__location_type=Location.LocationType.OWN,
            location__is_sellable=True,
            location__active=True,
        )
        .select_related("location")
        .order_by("-received_date", "-created_at")
        .first()
    )
    if latest is not None:
        return latest.location
    return (
        Location.objects
        .filter(location_type=Location.LocationType.OWN, is_sellable=True, active=True)
        .order_by("name")
        .first()
    )


def _add_to_stock(*, actor, product, quantity, reference):
    from .models import StockBatch
    from .services import post_stock_movement

    location = _warehouse_for_increase(product)
    if location is None:
        return None

    batch = StockBatch(
        product=product,
        location=location,
        quantity_received=quantity,
        quantity_remaining=quantity,
        reference=reference,
        created_by=actor,
        updated_by=actor,
    )
    batch.full_clean()
    batch.save()

    post_stock_movement(
        actor=actor,
        product=product,
        quantity=quantity,
        movement_type=StockMovement.MovementType.ADJUSTMENT,
        channel=StockMovement.Channel.SHOPIFY,
        from_location=None,
        to_location=location,
        reference=reference,
        batch=batch,
    )
    return location


def pull_manual_changes(*, actor, state, snapshot, snapshot_at, summary):
    """Shopify count now = what we last set - what its orders took + any
    change made by hand. Solving for the last term gives the manual
    change, which is recorded in stock exactly like any other movement."""
    from apps.products.models import Product

    if not snapshot:
        return

    for product in Product.objects.filter(pk__in=snapshot.keys()):
        now = snapshot[product.pk]
        sold = _sold_on_shopify_between(product, state.last_pushed_at, snapshot_at)
        change = now - product.shopify_pushed_quantity + sold

        if change == 0:
            continue

        reference = (
            f"Changed by hand on Shopify ({product.shopify_pushed_quantity} → {now}"
            + (f", after {sold} sold" if sold else "")
            + ")"
        )

        with transaction.atomic():
            if change < 0:
                taken = take_from_stock(
                    actor=actor,
                    product=product,
                    quantity=-change,
                    movement_type=StockMovement.MovementType.ADJUSTMENT,
                    reference=reference,
                )
                where = "taken out of stock"
                applied = -taken
                if taken < -change:
                    summary["warnings"].append(
                        f"{product.name}: Shopify was lowered by {-change} but only "
                        f"{taken.normalize():f} was in sellable warehouses."
                    )
            else:
                location = _add_to_stock(
                    actor=actor, product=product, quantity=Decimal(change), reference=reference
                )
                if location is None:
                    summary["warnings"].append(
                        f"{product.name}: Shopify was raised by {change} but there's no "
                        "sellable warehouse to put it in."
                    )
                    continue
                where = f"added to {location.name}"
                applied = Decimal(change)

            record_audit_event(
                user=actor,
                action="inventory.shopify_manual_change_recorded",
                instance=product,
                before_data={"shopify_pushed_quantity": product.shopify_pushed_quantity},
                after_data={
                    "shopify_quantity": now,
                    "sold_on_shopify": sold,
                    "change": str(applied),
                },
                reason=reference,
            )

        sign = "+" if applied > 0 else ""
        summary["manual_changes"].append(
            f"{product.name}: {sign}{applied.normalize():f} {where}"
        )


def link_inventory_items(*, summary=None):
    """Looks up the Shopify inventory item for each product imported
    from Shopify that isn't linked yet, and switches on Shopify's stock tracking for
    it — without tracking, Shopify ignores the quantity and never stops
    selling."""
    from apps.products.models import Product

    warnings = summary["warnings"] if summary is not None else []

    for product in Product.objects.exclude(shopify_product_id="").filter(
        shopify_inventory_item_id=""
    ):
        try:
            item_id, tracked, variant_count = shopify.inventory_item_for_product(
                product.shopify_product_id
            )
            if not tracked:
                shopify.track_inventory(item_id)
        except shopify.ShopifyError as exc:
            warnings.append(f"{product.name}: {exc}")
            continue

        if variant_count > 1:
            warnings.append(
                f"{product.name} has {variant_count} variants on Shopify; "
                "only the first one gets this product's stock."
            )

        if product.shopify_inventory_item_id != item_id:
            product.shopify_inventory_item_id = item_id
            product.save(update_fields=["shopify_inventory_item_id", "updated_at"])


def push_quantities(*, actor, state, summary):
    from apps.products.models import Product

    link_inventory_items(summary=summary)

    products = list(Product.objects.exclude(shopify_inventory_item_id=""))
    totals = sellable_quantities(products)

    quantities = {
        product.shopify_inventory_item_id: int(totals.get(product.pk, Decimal("0")))
        for product in products
    }

    shopify.set_available(
        quantities,
        state.location_id,
        reference=f"distribution-system://sync/{timezone.now():%Y-%m-%dT%H:%M:%S}",
    )
    summary["products_pushed"] = len(quantities)

    for product in products:
        pushed = quantities[product.shopify_inventory_item_id]
        if product.shopify_pushed_quantity != pushed:
            product.shopify_pushed_quantity = pushed
            product.save(update_fields=["shopify_pushed_quantity", "updated_at"])

    record_audit_event(
        user=actor,
        action="inventory.shopify_quantities_pushed",
        instance=state,
        after_data={
            "location": state.location_name,
            "quantities": {
                product.sku: quantities[product.shopify_inventory_item_id]
                for product in products
            },
        },
    )


def status_rows():
    """One row per product linked to Shopify: this system's sellable
    total next to what Shopify shows right now."""
    from apps.products.models import Product

    products = list(Product.objects.exclude(shopify_product_id="").order_by("name"))
    totals = sellable_quantities(products)
    state = ShopifySyncState.load()

    live = {}
    error = ""
    if shopify.is_configured() and state.location_id:
        try:
            live = shopify.fetch_available(
                [p.shopify_inventory_item_id for p in products if p.shopify_inventory_item_id],
                state.location_id,
            )
        except shopify.ShopifyError as exc:
            error = str(exc)

    rows = []
    for product in products:
        ours = int(totals.get(product.pk, Decimal("0")))
        theirs = live.get(product.shopify_inventory_item_id)
        rows.append(
            {
                "product": product,
                "ours": ours,
                "theirs": theirs,
                "in_step": theirs is not None and theirs == ours,
            }
        )

    return rows, error


def products_to_publish():
    """Active products with stock in a sellable warehouse that aren't on
    Shopify yet — what the next sync creates (or links)."""
    from apps.products.models import Product

    totals = sellable_quantities()
    in_stock = [pk for pk, quantity in totals.items() if quantity > 0]
    products = (
        Product.objects
        .filter(active=True, shopify_product_id="", pk__in=in_stock)
        .select_related("brand", "category")
        .order_by("name")
    )
    return [(product, int(totals[product.pk])) for product in products]


def match_on_shopify(product, shopify_products):
    """(Shopify product, why) that's already this product — same SKU or
    same handle — or (None, "")."""
    sku = product.sku.strip().upper()
    for candidate in shopify_products:
        if sku and sku in candidate["skus"]:
            return candidate, "same SKU"
    for candidate in shopify_products:
        if product.handle and candidate["handle"] == product.handle:
            return candidate, "same handle"
    return None, ""


def product_input(product, quantity, location_id, image_source=None):
    """This product in Shopify's fields — the same columns the Shopify
    CSV export fills."""
    variant = {
        "optionValues": [{"optionName": "Title", "name": "Default Title"}],
        "price": str(product.base_retail_price),
        "sku": product.sku,
        "inventoryPolicy": "DENY",
        "inventoryItem": {"tracked": True},
        "inventoryQuantities": [
            {"locationId": location_id, "name": "available", "quantity": quantity}
        ],
    }
    if product.compare_at_price is not None:
        variant["compareAtPrice"] = str(product.compare_at_price)
    if product.barcode:
        variant["barcode"] = product.barcode
    if product.weight_grams:
        variant["inventoryItem"]["measurement"] = {
            "weight": {"unit": "GRAMS", "value": float(product.weight_grams)}
        }

    data = {
        "title": product.name,
        "descriptionHtml": product.body_html or "",
        "status": "ACTIVE",
        "productOptions": [{"name": "Title", "values": [{"name": "Default Title"}]}],
        "variants": [variant],
        "tags": [tag.strip() for tag in (product.tags or "").split(",") if tag.strip()],
    }
    if product.handle:
        data["handle"] = product.handle
    # The brand, never the contract manufacturer — Vendor is public.
    vendor = product.brand.name if product.brand_id else product.brand_name
    if vendor:
        data["vendor"] = vendor
    if product.category_id:
        data["productType"] = product.category.name
    if image_source:
        data["files"] = [
            {"originalSource": image_source, "contentType": "IMAGE", "alt": product.name}
        ]
    return data


def _image_source(product, warnings):
    if not product.image:
        return None
    try:
        with product.image.open("rb") as image:
            content = image.read()
        filename = os.path.basename(product.image.name)
        mime_type = mimetypes.guess_type(filename)[0] or "image/jpeg"
        return shopify.upload_image(filename, content, mime_type)
    except (OSError, shopify.ShopifyError) as exc:
        warnings.append(f"{product.name}: created without its image ({exc}).")
        return None


def link_product(*, actor, product, shopify_product_id, how=""):
    """Points one product here at an existing Shopify product. A Shopify
    product only ever belongs to one product here, so any other product
    linked to it is unlinked (its stock and history stay as they are)."""
    from apps.products.models import Product

    previous = list(
        Product.objects.filter(shopify_product_id=shopify_product_id).exclude(pk=product.pk)
    )
    for other in previous:
        other.shopify_product_id = ""
        other.shopify_inventory_item_id = ""
        other.save(update_fields=["shopify_product_id", "shopify_inventory_item_id", "updated_at"])

    product.shopify_product_id = shopify_product_id
    product.shopify_inventory_item_id = ""
    product.save(update_fields=["shopify_product_id", "shopify_inventory_item_id", "updated_at"])

    record_audit_event(
        user=actor,
        action="products.shopify_product_linked",
        instance=product,
        after_data={
            "shopify_product_id": shopify_product_id,
            "how": how,
            "unlinked_products": [other.sku for other in previous],
        },
    )


def publish_missing_products(*, actor, state, scopes, summary):
    candidates = products_to_publish()
    if not candidates:
        return

    missing = [scope for scope in shopify.PRODUCT_PUBLISH_SCOPES if scope not in scopes]
    if missing:
        summary["warnings"].append(
            f"{len(candidates)} product(s) with stock aren't on Shopify yet. To create "
            "them, add " + ", ".join(missing) + " to the Shopify app, release the "
            "version and approve it on the store."
        )
        return

    shopify_products = shopify.list_products()
    publication_id = shopify.online_store_publication_id()
    if publication_id is None:
        summary["warnings"].append(
            "No Online Store sales channel on Shopify — new products were created "
            "but not put on the storefront."
        )

    for product, quantity in candidates:
        match, how = match_on_shopify(product, shopify_products)
        if match is not None:
            link_product(actor=actor, product=product, shopify_product_id=match["id"], how=how)
            summary["products_linked"] += 1
            continue

        try:
            image_source = _image_source(product, summary["warnings"])
            product_id, product_gid, item_id = shopify.create_product(
                product_input(product, quantity, state.location_id, image_source)
            )
            if publication_id:
                shopify.publish(product_gid, publication_id)
        except shopify.ShopifyError as exc:
            summary["warnings"].append(f"{product.name}: couldn't create on Shopify ({exc}).")
            continue

        product.shopify_product_id = product_id
        product.shopify_inventory_item_id = item_id
        product.save(update_fields=["shopify_product_id", "shopify_inventory_item_id", "updated_at"])
        summary["products_created"] += 1

        record_audit_event(
            user=actor,
            action="products.shopify_product_created",
            instance=product,
            after_data={
                "shopify_product_id": product_id,
                "quantity": quantity,
                "price": str(product.base_retail_price),
                "published": bool(publication_id),
            },
        )
