"""Owner-only finance: the sales ledger, expenses, market research, the
Product Finance sheet and the daily / weekly / monthly reports.

Everything starts from three things the rest of the system already owns:

* the Batch — one Manufacturer order line, so it knows what a unit cost
  us (its landed cost, see landed_unit_cost);
* the Product and its prices — the retail price per bottle size;
* the stock ledger — every movement that takes stock out of the Owner's
  hands becomes one immutable SaleEntry (see record_sale).

Formulas (per unit unless it says total):

    landed cost        = manufacturer price × (1 + order tax %)
                         + order shipping × manufacturer price ÷ order subtotal
    distributor cut    = retail price × distributor cut %
    we receive         = retail price − distributor cut
    gross profit       = (we receive − cost) × quantity             [total]
    expense share %    = this row's gross profit ÷ all rows' gross profit
    expense            = expense share % × total expenses ÷ quantity
    profit             = we receive − cost − expense
    profit %           = profit ÷ retail price
    total profit       = profit × quantity
    investment         = cost × quantity                            [total]
    retail sale value  = retail price × quantity                    [total]
    net profit         = gross profit − expenses − free stock − write-offs
"""

import calendar
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.services import require_owner
from apps.audit.services import record_audit_event
from apps.products.models import BottleSize

from .models import Expense, FinanceSettings, MarketResearch, SaleEntry

ZERO = Decimal("0")
HUNDRED = Decimal("100")
UNIT_COST_PLACES = Decimal("0.0001")
MONEY_PLACES = Decimal("0.01")


# ---------------------------------------------------------------------------
# Costs and prices
# ---------------------------------------------------------------------------


def landed_unit_cost(item):
    """What one unit of a Manufacturer order line really cost: its price
    plus its share of the order's tax and shipping. Shipping is shared by
    value, so a Rs 1,000 bottle carries ten times the shipping of a
    Rs 100 one. None when the line has no agreed price yet."""
    if item.unit_price is None:
        return None

    order = item.order
    cost = item.unit_price * (1 + order.tax_percentage / HUNDRED)

    if order.shipping_amount:
        lines = [line for line in order.items.all() if line.unit_price is not None]
        subtotal = sum((line.quantity * line.unit_price for line in lines), ZERO)
        units = sum((line.quantity for line in lines), ZERO)

        if subtotal > 0:
            cost += order.shipping_amount * item.unit_price / subtotal
        elif units > 0:
            cost += order.shipping_amount / units

    return cost.quantize(UNIT_COST_PLACES)


def _product_bottle_size(product):
    if product.size is None:
        return None
    size = int(product.size)
    return size if size == product.size and size in BottleSize.values else None


def bottle_size_for(product, stock_batch=None):
    """The capsule count of the bottles in a batch: what the Manufacturer
    order line said, else the product's own size when it's a standard
    bottle size."""
    source = stock_batch.source_batch if stock_batch is not None else None
    if source is not None and source.manufacturer_order_item.bottle_size:
        return source.manufacturer_order_item.bottle_size
    return _product_bottle_size(product)


def unit_cost_for(product, stock_batch=None, bottle_size=None):
    """(unit cost, CostSource). The batch's own landed cost when it came
    from a Manufacturer order; otherwise the product's price list (stock
    added by hand); otherwise unknown, counted as 0 and flagged."""
    source = stock_batch.source_batch if stock_batch is not None else None
    if source is not None:
        cost = landed_unit_cost(source.manufacturer_order_item)
        if cost is not None:
            return cost, SaleEntry.CostSource.BATCH

    if bottle_size is None:
        bottle_size = _product_bottle_size(product)

    price = None
    if bottle_size is not None:
        price = (
            product.bottle_prices.filter(bottle_size=bottle_size)
            .values_list("price", flat=True)
            .first()
        )
    if price is None and product.size:
        price = (
            product.package_prices.filter(amount=product.size)
            .order_by("price")
            .values_list("price", flat=True)
            .first()
        )
    if price is not None:
        return Decimal(price).quantize(UNIT_COST_PLACES), SaleEntry.CostSource.PRICE_LIST

    return ZERO, SaleEntry.CostSource.UNKNOWN


def retail_price_for(product, bottle_size=None):
    """Our retail price for one bottle: the price set for that bottle
    size, else the product's base retail price."""
    if bottle_size is not None:
        price = (
            product.retail_prices.filter(bottle_size=bottle_size)
            .values_list("price", flat=True)
            .first()
        )
        if price is not None:
            return price
    return product.base_retail_price


def market_price_for(product, bottle_size, research_rows):
    """What a pack the size of ours sells for elsewhere: the average
    price per capsule (or gram, ml…) of the research rows marked "use",
    times our pack size. Falls back to their average pack price when we
    don't know our own pack size. None without research."""
    rows = [
        row for row in research_rows
        if row.product_id == product.pk and row.use_for_market_price
    ]
    if not rows:
        return None

    pack = bottle_size or product.size
    if pack:
        per_unit = sum((row.price_per_unit for row in rows), ZERO) / len(rows)
        return (per_unit * Decimal(pack)).quantize(MONEY_PLACES)

    return (sum((row.market_price for row in rows), ZERO) / len(rows)).quantize(MONEY_PLACES)


# ---------------------------------------------------------------------------
# The sales ledger
# ---------------------------------------------------------------------------


_PO_REFERENCE = re.compile(r"Purchase order (\S+) shipped")


def _kind_for(movement):
    """Which kind of finance entry a stock movement is, or None when it
    isn't one: stock arriving, moving between the Owner's own warehouses,
    or leaving Transit (already counted the moment it was shipped)."""
    from apps.owner_inventory.models import StockMovement
    from apps.owner_warehouse.models import Location

    if movement.from_location_id is None:
        return None

    if movement.from_location.location_type == Location.LocationType.TRANSIT:
        return None

    if movement.to_location_id is not None:
        if (
            movement.to_location.location_type == Location.LocationType.TRANSIT
            and movement.channel == StockMovement.Channel.DISTRIBUTOR
        ):
            return SaleEntry.Kind.DISTRIBUTOR_SALE
        return None

    if movement.movement_type == StockMovement.MovementType.SALE:
        return SaleEntry.Kind.SHOPIFY_SALE

    if movement.channel == StockMovement.Channel.DISTRIBUTOR:
        return SaleEntry.Kind.GIFT

    return SaleEntry.Kind.WRITE_OFF


def _purchase_order_item_for(movement):
    """For entries rebuilt from movements made before the ledger existed:
    the shipment's purchase order line, read from the movement's
    reference."""
    from apps.requests.models import PurchaseOrderItem

    match = _PO_REFERENCE.search(movement.reference or "")
    if not match:
        return None
    return (
        PurchaseOrderItem.objects.select_related("purchase_order")
        .filter(purchase_order__po_number=match.group(1), product=movement.product_id)
        .first()
    )


def _shopify_line_for(movement):
    from apps.owner_inventory.models import ShopifyOrderLine

    if movement.shopify_order_id is None:
        return None
    return ShopifyOrderLine.objects.filter(
        order=movement.shopify_order_id, product=movement.product_id
    ).first()


@transaction.atomic
def record_sale(*, actor, movement, purchase_order_item=None, shopify_order_line=None):
    """Writes the finance ledger entry for one stock movement — called in
    the same transaction as the movement, by every place stock leaves the
    Owner's hands. Idempotent: a movement already in the ledger returns
    its entry. Returns None for movements that aren't sales or losses."""
    existing = SaleEntry.objects.filter(movement=movement).first()
    if existing is not None:
        return existing

    kind = _kind_for(movement)
    if kind is None:
        return None

    product = movement.product
    stock_batch = movement.batch
    source_batch = stock_batch.source_batch if stock_batch is not None else None
    bottle_size = bottle_size_for(product, stock_batch)
    unit_cost, cost_source = unit_cost_for(product, stock_batch, bottle_size)
    retail = retail_price_for(product, bottle_size)
    distributor_profile = movement.distributor_profile
    net = ZERO

    if kind == SaleEntry.Kind.DISTRIBUTOR_SALE:
        item = purchase_order_item or _purchase_order_item_for(movement)
        if item is not None:
            retail = item.list_price or retail
            net = item.unit_price if item.unit_price is not None else retail
            distributor_profile = distributor_profile or item.purchase_order.distributor_profile
        else:
            cut = FinanceSettings.load().distributor_cut_percentage
            net = (retail * (1 - cut / HUNDRED)).quantize(MONEY_PLACES)
        purchase_order_item = item

    elif kind == SaleEntry.Kind.SHOPIFY_SALE:
        line = shopify_order_line or _shopify_line_for(movement)
        if line is not None:
            if line.original_unit_price is not None:
                retail = line.original_unit_price
            net = line.unit_price if line.unit_price is not None else retail
        else:
            net = retail
        shopify_order_line = line

    entry = SaleEntry(
        movement=movement,
        kind=kind,
        occurred_at=movement.created_at,
        occurred_on=timezone.localdate(movement.created_at),
        product=product,
        bottle_size=bottle_size,
        stock_batch=stock_batch,
        source_batch=source_batch,
        batch_number=stock_batch.batch_number if stock_batch is not None else "",
        distributor_profile=distributor_profile,
        purchase_order_item=purchase_order_item if kind == SaleEntry.Kind.DISTRIBUTOR_SALE else None,
        shopify_order_line=shopify_order_line if kind == SaleEntry.Kind.SHOPIFY_SALE else None,
        quantity=movement.quantity,
        unit_cost=unit_cost,
        cost_source=cost_source,
        retail_unit_price=retail,
        net_unit_price=net,
        reference=movement.reference[:255],
        created_by=actor,
        updated_by=actor,
    )
    entry.full_clean(validate_unique=False)
    try:
        # A savepoint, so losing a race with another request recording
        # the same movement doesn't break the caller's transaction.
        with transaction.atomic():
            entry.save()
    except IntegrityError:
        return SaleEntry.objects.get(movement=movement)

    return entry


def sync_sales_ledger(*, actor=None):
    """Adds a ledger entry for every stock-out movement that doesn't have
    one — movements made before the ledger existed, or by a path that
    doesn't record its own. Safe to run any time; returns how many it
    added."""
    from apps.owner_inventory.models import StockMovement
    from apps.owner_warehouse.models import Location

    transit = Location.LocationType.TRANSIT
    missing = (
        StockMovement.objects
        .filter(from_location__isnull=False, sale_entry__isnull=True)
        .exclude(from_location__location_type=transit)
        .filter(Q(to_location__isnull=True) | Q(to_location__location_type=transit))
        .select_related(
            "product", "from_location", "to_location", "distributor_profile",
            "batch__source_batch__manufacturer_order_item__order",
        )
        .order_by("created_at")
    )

    added = 0
    for movement in missing:
        if record_sale(actor=actor, movement=movement) is not None:
            added += 1
    return added


# ---------------------------------------------------------------------------
# Expenses, market research, settings
# ---------------------------------------------------------------------------


def _snapshot(instance, fields):
    return {name: str(getattr(instance, name)) for name in fields}


EXPENSE_FIELDS = ("date", "name", "category", "amount", "comments")
RESEARCH_FIELDS = (
    "product_id", "similar_product", "store", "pack_size", "unit",
    "market_price", "use_for_market_price", "checked_on", "note", "link",
)


@transaction.atomic
def save_expense(*, actor, expense, before=None):
    require_owner(actor)

    is_new = expense._state.adding
    if is_new:
        expense.created_by = actor
    expense.updated_by = actor
    expense.full_clean()
    expense.save()

    record_audit_event(
        user=actor,
        action="finance.expense_created" if is_new else "finance.expense_updated",
        instance=expense,
        before_data=before,
        after_data=_snapshot(expense, EXPENSE_FIELDS),
    )
    return expense


@transaction.atomic
def delete_expense(*, actor, expense):
    require_owner(actor)

    before = _snapshot(expense, EXPENSE_FIELDS)
    record_audit_event(
        user=actor,
        action="finance.expense_deleted",
        instance=expense,
        before_data=before,
    )
    expense.delete()


@transaction.atomic
def save_market_research(*, actor, research, before=None):
    require_owner(actor)

    is_new = research._state.adding
    if is_new:
        research.created_by = actor
    research.updated_by = actor
    research.full_clean()
    research.save()

    record_audit_event(
        user=actor,
        action="finance.market_research_created" if is_new else "finance.market_research_updated",
        instance=research,
        before_data=before,
        after_data=_snapshot(research, RESEARCH_FIELDS),
    )
    return research


@transaction.atomic
def delete_market_research(*, actor, research):
    require_owner(actor)

    record_audit_event(
        user=actor,
        action="finance.market_research_deleted",
        instance=research,
        before_data=_snapshot(research, RESEARCH_FIELDS),
    )
    research.delete()


@transaction.atomic
def update_distributor_cut(*, actor, percentage):
    require_owner(actor)

    try:
        percentage = Decimal(str(percentage))
    except Exception as exc:
        raise ValidationError("Enter the distributor cut as a number.") from exc

    settings = FinanceSettings.load()
    before = {"distributor_cut_percentage": str(settings.distributor_cut_percentage)}
    settings.distributor_cut_percentage = percentage
    settings.full_clean()
    settings.save()

    record_audit_event(
        user=actor,
        action="finance.settings_updated",
        instance=settings,
        before_data=before,
        after_data={"distributor_cut_percentage": str(percentage)},
    )
    return settings


# ---------------------------------------------------------------------------
# The Product Finance sheet
# ---------------------------------------------------------------------------


@dataclass
class SheetRow:
    """One line of the Product Finance sheet — a batch, or every batch of
    one product and bottle size added together."""

    product: object
    bottle_size: int | None
    unit_cost: Decimal
    retail_price: Decimal
    quantity: Decimal
    market_price: Decimal | None = None
    batches: list = field(default_factory=list)

    distributor_cut: Decimal = ZERO
    we_receive: Decimal = ZERO
    gross_profit: Decimal = ZERO
    expense_share: Decimal = ZERO
    expense_per_unit: Decimal = ZERO
    profit_per_unit: Decimal = ZERO
    profit_percentage: Decimal | None = None
    total_profit: Decimal = ZERO

    @property
    def investment(self):
        return self.unit_cost * self.quantity

    @property
    def retail_value(self):
        return self.retail_price * self.quantity

    @property
    def distributor_cut_total(self):
        return self.distributor_cut * self.quantity

    @property
    def we_receive_total(self):
        return self.we_receive * self.quantity

    @property
    def expense_total(self):
        return self.expense_per_unit * self.quantity

    @property
    def label(self):
        size = f" ({self.bottle_size} caps)" if self.bottle_size else ""
        return f"{self.product.name}{size}"


@dataclass
class SheetTotals:
    quantity: Decimal = ZERO
    investment: Decimal = ZERO
    retail_value: Decimal = ZERO
    distributor_cut: Decimal = ZERO
    we_receive: Decimal = ZERO
    gross_profit: Decimal = ZERO
    expense_share: Decimal = ZERO
    expenses: Decimal = ZERO
    net_profit: Decimal = ZERO

    @property
    def net_margin(self):
        return self.net_profit / self.retail_value * HUNDRED if self.retail_value else None


def compute_product_finance(rows, *, total_expenses, distributor_cut_percentage):
    """Fills in every calculated column of the Product Finance sheet, the
    same way the spreadsheet does. Expenses are shared by each row's
    gross profit (we receive − cost, × quantity), so the products earning
    the most carry the most overhead. Rows that lose money before
    expenses carry none; if nothing makes a gross profit, expenses are
    shared by retail value instead."""
    cut_rate = Decimal(distributor_cut_percentage) / HUNDRED
    total_expenses = Decimal(total_expenses)

    for row in rows:
        row.distributor_cut = row.retail_price * cut_rate
        row.we_receive = row.retail_price - row.distributor_cut
        row.gross_profit = (row.we_receive - row.unit_cost) * row.quantity

    basis = {id(row): max(row.gross_profit, ZERO) for row in rows}
    basis_total = sum(basis.values(), ZERO)
    if basis_total <= 0:
        basis = {id(row): row.retail_value for row in rows}
        basis_total = sum(basis.values(), ZERO)

    totals = SheetTotals(expenses=total_expenses if rows else ZERO)

    for row in rows:
        row.expense_share = basis[id(row)] / basis_total if basis_total > 0 else ZERO
        row.expense_per_unit = (
            row.expense_share * total_expenses / row.quantity if row.quantity else ZERO
        )
        row.profit_per_unit = row.we_receive - row.unit_cost - row.expense_per_unit
        row.profit_percentage = (
            row.profit_per_unit / row.retail_price * HUNDRED if row.retail_price else None
        )
        row.total_profit = row.profit_per_unit * row.quantity

        totals.quantity += row.quantity
        totals.investment += row.investment
        totals.retail_value += row.retail_value
        totals.distributor_cut += row.distributor_cut_total
        totals.we_receive += row.we_receive_total
        totals.gross_profit += row.gross_profit
        totals.expense_share += row.expense_share
        totals.net_profit += row.total_profit

    return totals


def _local_day_range(field_name, start, end):
    """A filter for a datetime field falling on local dates start..end."""
    filters = {}
    if start:
        filters[f"{field_name}__date__gte"] = start
    if end:
        filters[f"{field_name}__date__lte"] = end
    return filters


def product_finance_sheet(
    *, start=None, end=None, group="batch", include_pending=True, cut=None, product_id=None
):
    """The Product Finance sheet built from real data: one row per
    Manufacturer order batch (or per product and bottle size), its
    quantity and landed cost from the order, its retail price from the
    product, and the expenses dated in the same range shared across it.
    start / end filter batches by the day their order was placed."""
    from apps.batches.models import Batch

    batches = (
        Batch.objects
        .exclude(status=Batch.Status.CANCELLED)
        .filter(manufacturer_order_item__unit_price__isnull=False)
        .filter(**_local_day_range("created_at", start, end))
        .select_related(
            "manufacturer_order_item__product",
            "manufacturer_order_item__order__manufacturer",
        )
        .prefetch_related(
            "manufacturer_order_item__order__items",
            "manufacturer_order_item__product__retail_prices",
        )
        .order_by("manufacturer_order_item__product__name", "manufacturer_order_item__bottle_size", "created_at")
    )
    if not include_pending:
        batches = batches.filter(status=Batch.Status.RECEIVED)

    research = list(MarketResearch.objects.all())
    rows = OrderedDict()

    for batch in batches:
        item = batch.manufacturer_order_item
        product = item.product
        bottle_size = item.bottle_size or _product_bottle_size(product)
        cost = landed_unit_cost(item)
        key = batch.pk if group == "batch" else (product.pk, bottle_size)

        row = rows.get(key)
        if row is None:
            row = rows[key] = SheetRow(
                product=product,
                bottle_size=bottle_size,
                unit_cost=ZERO,
                retail_price=_prefetched_retail_price(product, bottle_size),
                quantity=ZERO,
                market_price=market_price_for(product, bottle_size, research),
            )

        # Weighted average cost when several batches share one row.
        combined = row.quantity + item.quantity
        row.unit_cost = (row.unit_cost * row.quantity + cost * item.quantity) / combined
        row.quantity = combined
        row.batches.append(batch)

    expenses = Expense.objects.all()
    if start:
        expenses = expenses.filter(date__gte=start)
    if end:
        expenses = expenses.filter(date__lte=end)
    total_expenses = sum((expense.amount for expense in expenses), ZERO)

    if cut is None:
        cut = FinanceSettings.load().distributor_cut_percentage

    rows = list(rows.values())
    totals = compute_product_finance(
        rows, total_expenses=total_expenses, distributor_cut_percentage=cut
    )

    if product_id:
        # Expenses are shared across every batch first, so one product
        # keeps only its own share of them rather than all of them.
        rows = [row for row in rows if str(row.product.pk) == str(product_id)]
        totals = _sheet_totals(rows)

    return rows, totals


def _sheet_totals(rows):
    totals = SheetTotals()
    for row in rows:
        totals.quantity += row.quantity
        totals.investment += row.investment
        totals.retail_value += row.retail_value
        totals.distributor_cut += row.distributor_cut_total
        totals.we_receive += row.we_receive_total
        totals.gross_profit += row.gross_profit
        totals.expense_share += row.expense_share
        totals.expenses += row.expense_total
        totals.net_profit += row.total_profit
    return totals


def _prefetched_retail_price(product, bottle_size):
    if bottle_size is not None:
        for price in product.retail_prices.all():
            if price.bottle_size == bottle_size:
                return price.price
    return product.base_retail_price


# ---------------------------------------------------------------------------
# Product Prices
# ---------------------------------------------------------------------------


@dataclass
class PriceRow:
    """One line of the Product Prices sheet: a product in one pack size
    from one Manufacturer."""

    product: object
    bottle_size: object
    manufacturer: object
    manufacturer_price: Decimal | None
    price_source: str
    latest_batch_cost: Decimal | None
    latest_batch: object
    retail_price: Decimal
    market_price: Decimal | None

    @property
    def cost(self):
        return self.latest_batch_cost if self.latest_batch_cost is not None else self.manufacturer_price

    @property
    def markup_percentage(self):
        if not self.cost:
            return None
        return (self.retail_price - self.cost) / self.cost * HUNDRED

    @property
    def vs_market_percentage(self):
        if not self.market_price:
            return None
        return (self.retail_price - self.market_price) / self.market_price * HUNDRED

    @property
    def manufacturer_name(self):
        return self.manufacturer.name if self.manufacturer else ""

    @property
    def pack_label(self):
        from apps.products.catalog import unit_plural

        if self.bottle_size is None:
            return self.product.pack_size or ""
        amount = f"{Decimal(self.bottle_size).normalize():f}"
        unit = unit_plural(self.product)
        # A bottle size is a capsule count; "60 bottle" would mislead.
        if unit in ("units", "bottle", "box", "case") and self.bottle_size in BottleSize.values:
            unit = "capsules"
        return f"{amount} {unit}"

    @property
    def dropdown_name(self):
        """e.g. "Saw Palmetto (30 capsules)(GL)" — product, pack and the
        Manufacturer's initials, as on the price sheet."""
        name = self.product.name
        if self.bottle_size is not None:
            name += f" ({self.pack_label})"
        if self.manufacturer:
            initials = "".join(word[0] for word in self.manufacturer.name.split() if word).upper()
            name += f"({initials})"
        return name

    @property
    def label(self):
        return f"{self.product.name} ({self.pack_label})" if self.bottle_size else self.product.name


def _pack_key(amount):
    """Package amounts are decimals (60.00); bottle sizes are whole
    numbers (60). One key for both, so the same pack lines up."""
    if amount is None:
        return None
    amount = Decimal(amount)
    return int(amount) if amount == amount.to_integral_value() else amount.normalize()


def product_price_rows():
    """The Product Prices sheet: every product at every pack size and
    Manufacturer it has a price for — the Manufacturer's quoted price
    (the product's package prices, else its bottle price list), what the
    latest batch of it actually cost landed, our retail price and the
    market price beside it."""
    from apps.batches.models import Batch
    from apps.products.models import Product

    products = (
        Product.objects.filter(active=True)
        .select_related("manufacturer")
        .prefetch_related("retail_prices", "bottle_prices", "package_prices__manufacturer")
        .order_by("name")
    )
    research = list(MarketResearch.objects.all())

    latest = {}
    for batch in (
        Batch.objects
        .exclude(status=Batch.Status.CANCELLED)
        .filter(manufacturer_order_item__unit_price__isnull=False)
        .select_related(
            "manufacturer_order_item__product",
            "manufacturer_order_item__order__manufacturer",
        )
        .prefetch_related("manufacturer_order_item__order__items")
        .order_by("created_at")
    ):
        item = batch.manufacturer_order_item
        size = item.bottle_size or _product_bottle_size(item.product)
        latest[(item.product_id, size, item.order.manufacturer_id)] = batch
        latest[(item.product_id, size, None)] = batch

    rows = []
    for product in products:
        retail = {price.bottle_size: price.price for price in product.retail_prices.all()}
        quoted = OrderedDict()  # (pack, manufacturer id) -> (manufacturer, price)

        for package in product.package_prices.all():
            quoted[(_pack_key(package.amount), package.manufacturer_id)] = (package.manufacturer, package.price)
        for price in product.bottle_prices.all():
            key = (price.bottle_size, product.manufacturer_id)
            quoted.setdefault(key, (product.manufacturer, price.price))

        ordered = {
            (size, manufacturer_id)
            for (product_id, size, manufacturer_id) in latest
            if product_id == product.pk and manufacturer_id is not None
        }
        keys = set(quoted) | ordered
        priced_sizes = {size for size, _ in keys}
        keys |= {(size, product.manufacturer_id) for size in retail if size not in priced_sizes}
        if not keys:
            keys = {(None, product.manufacturer_id)}

        for size, manufacturer_id in sorted(
            keys, key=lambda key: (key[0] is not None, key[0] or 0, str(key[1]))
        ):
            batch = latest.get((product.pk, size, manufacturer_id)) or latest.get((product.pk, size, None))
            manufacturer, price = quoted.get((size, manufacturer_id), (None, None))
            price_source = "Price list" if price is not None else ""
            if price is None and batch is not None:
                # No price list entered: the price on the latest order is
                # what the Manufacturer actually charges.
                price = batch.manufacturer_order_item.unit_price
                price_source = f"Order {batch.manufacturer_order_item.order.po_number}"
            if manufacturer is None and batch is not None:
                manufacturer = batch.manufacturer_order_item.order.manufacturer
            if manufacturer is None and manufacturer_id == product.manufacturer_id:
                manufacturer = product.manufacturer

            rows.append(
                PriceRow(
                    product=product,
                    bottle_size=size,
                    manufacturer=manufacturer,
                    manufacturer_price=price,
                    price_source=price_source,
                    latest_batch_cost=(
                        landed_unit_cost(batch.manufacturer_order_item) if batch else None
                    ),
                    latest_batch=batch,
                    retail_price=retail.get(size, product.base_retail_price),
                    market_price=market_price_for(
                        product, size or _product_bottle_size(product), research
                    ),
                )
            )
    return rows


# ---------------------------------------------------------------------------
# Daily / weekly / monthly reports
# ---------------------------------------------------------------------------


PERIODS = ("daily", "weekly", "monthly", "all", "custom")


def first_activity_date():
    """The earliest day anything money-related happened — the start of
    an "all time" report."""
    from apps.batches.models import Batch

    candidates = [
        SaleEntry.objects.order_by("occurred_on").values_list("occurred_on", flat=True).first(),
        Expense.objects.order_by("date").values_list("date", flat=True).first(),
    ]
    received = (
        Batch.objects.filter(received_at__isnull=False)
        .order_by("received_at").values_list("received_at", flat=True).first()
    )
    if received:
        candidates.append(timezone.localdate(received))
    candidates = [day for day in candidates if day]
    return min(candidates) if candidates else timezone.localdate()


def latest_sale_date(**filters):
    return (
        _filtered_entries(SaleEntry.objects.all(), **filters)
        .order_by("-occurred_on").values_list("occurred_on", flat=True).first()
    )


def _filtered_entries(entries, *, kind=None, product_id=None, distributor_id=None, batch_number=None):
    if kind:
        entries = entries.filter(kind=kind)
    if product_id:
        entries = entries.filter(product_id=product_id)
    if distributor_id:
        entries = entries.filter(distributor_profile_id=distributor_id)
    if batch_number:
        entries = entries.filter(batch_number=batch_number)
    return entries


def period_bounds(period, anchor, start=None, end=None):
    """(start, end, previous anchor, next anchor) for a report period
    containing anchor. Weeks run Monday to Sunday."""
    if period == "daily":
        return anchor, anchor, anchor - timedelta(days=1), anchor + timedelta(days=1)

    if period == "weekly":
        monday = anchor - timedelta(days=anchor.weekday())
        return monday, monday + timedelta(days=6), monday - timedelta(days=7), monday + timedelta(days=7)

    if period == "monthly":
        first = anchor.replace(day=1)
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
        previous = (first - timedelta(days=1)).replace(day=1)
        following = last + timedelta(days=1)
        return first, last, previous, following

    start = start or anchor
    end = end or start
    if end < start:
        start, end = end, start
    return start, end, None, None


@dataclass
class Figures:
    """Money totals for any slice of the ledger — a day, a product, a
    channel, a whole period."""

    units_sold: Decimal = ZERO
    retail_value: Decimal = ZERO
    cut: Decimal = ZERO
    revenue: Decimal = ZERO
    cogs: Decimal = ZERO
    free_units: Decimal = ZERO
    free_cost: Decimal = ZERO
    written_off_units: Decimal = ZERO
    written_off_cost: Decimal = ZERO
    expenses: Decimal = ZERO
    unknown_cost_units: Decimal = ZERO

    def add(self, entry):
        if entry.cost_source == SaleEntry.CostSource.UNKNOWN:
            self.unknown_cost_units += entry.quantity

        if entry.is_sale:
            self.units_sold += entry.quantity
            self.retail_value += entry.retail_value
            self.cut += entry.cut
            self.revenue += entry.revenue
            self.cogs += entry.cost
        elif entry.kind == SaleEntry.Kind.GIFT:
            self.free_units += entry.quantity
            self.free_cost += entry.cost
        else:
            self.written_off_units += entry.quantity
            self.written_off_cost += entry.cost

    @property
    def gross_profit(self):
        return self.revenue - self.cogs

    @property
    def lost_cost(self):
        """Cost of stock that brought in nothing: given free or written off."""
        return self.free_cost + self.written_off_cost

    @property
    def gross_margin(self):
        return self.gross_profit / self.revenue * HUNDRED if self.revenue else None

    @property
    def net_profit(self):
        return self.gross_profit - self.expenses - self.free_cost - self.written_off_cost

    @property
    def net_margin(self):
        return self.net_profit / self.revenue * HUNDRED if self.revenue else None


@dataclass
class Breakdown:
    label: str
    figures: Figures = field(default_factory=Figures)
    detail: str = ""
    key: object = None


def _grouped(entries, key_for, label_for, detail_for=lambda entry: ""):
    groups = OrderedDict()
    for entry in entries:
        key = key_for(entry)
        if key not in groups:
            groups[key] = Breakdown(label=label_for(entry), detail=detail_for(entry), key=key)
        groups[key].figures.add(entry)
    return sorted(groups.values(), key=lambda group: group.figures.revenue, reverse=True)


def _size_label(entry):
    return f" ({entry.bottle_size} caps)" if entry.bottle_size else ""


def _buckets(start, end):
    """The rows a period's timeline is split into: days, or months for
    ranges longer than about two months."""
    if (end - start).days > 62:
        buckets = []
        cursor = start.replace(day=1)
        while cursor <= end:
            last = cursor.replace(day=calendar.monthrange(cursor.year, cursor.month)[1])
            buckets.append((max(cursor, start), min(last, end), cursor.strftime("%B %Y")))
            cursor = last + timedelta(days=1)
        return buckets

    days = (end - start).days + 1
    return [
        (day, day, day.strftime("%a %d %b"))
        for day in (start + timedelta(days=offset) for offset in range(days))
    ]


@dataclass
class PeriodReport:
    start: date
    end: date
    totals: Figures
    entries: list
    expenses: list
    timeline: list
    by_kind: list
    by_product: list
    by_batch: list
    by_distributor: list
    expenses_by_category: list
    stock_bought: Decimal
    stock_bought_units: Decimal
    paid_to_manufacturers: Decimal
    received_from_distributors: Decimal
    filtered: bool = False

    @property
    def has_activity(self):
        return bool(self.entries or self.expenses or self.stock_bought)

    @property
    def columns(self):
        """Which optional columns have anything in them this period, so
        the tables (and downloads) leave out the ones that would only
        ever say 0."""
        return {
            "cut": bool(self.totals.cut),
            "lost": bool(self.totals.lost_cost),
            "expenses": bool(self.totals.expenses),
        }


def period_report(start, end, *, kind=None, product_id=None, distributor_id=None, batch_number=None):
    """Everything that happened to money between start and end
    (inclusive local dates), straight from the ledger.

    The filters narrow the sales figures to one channel, product,
    Distributor or batch. Expenses belong to the whole business, not to
    one product, so a filtered report leaves them out (its net profit is
    the profit that slice contributed before overheads)."""
    from apps.batches.models import Batch
    from apps.manufacturers.models import ManufacturerOrderPayment
    from apps.requests.models import PurchaseOrderPayment

    filtered = any([kind, product_id, distributor_id, batch_number])
    entries = list(
        _filtered_entries(
            SaleEntry.objects.between(start, end),
            kind=kind, product_id=product_id, distributor_id=distributor_id, batch_number=batch_number,
        )
        .select_related("product", "distributor_profile", "source_batch")
        .order_by("occurred_at")
    )
    expenses = (
        [] if filtered
        else list(Expense.objects.filter(date__gte=start, date__lte=end).order_by("date"))
    )

    totals = Figures()
    for entry in entries:
        totals.add(entry)
    totals.expenses = sum((expense.amount for expense in expenses), ZERO)

    timeline = []
    for bucket_start, bucket_end, label in _buckets(start, end):
        figures = Figures()
        for entry in entries:
            if bucket_start <= entry.occurred_on <= bucket_end:
                figures.add(entry)
        figures.expenses = sum(
            (expense.amount for expense in expenses if bucket_start <= expense.date <= bucket_end),
            ZERO,
        )
        timeline.append(Breakdown(label=label, figures=figures, key=bucket_start))

    by_category = OrderedDict()
    for expense in expenses:
        by_category.setdefault(expense.get_category_display(), ZERO)
        by_category[expense.get_category_display()] += expense.amount

    bought_units = ZERO
    bought = ZERO
    received = Batch.objects.filter(status=Batch.Status.RECEIVED, **_local_day_range("received_at", start, end))
    if product_id:
        received = received.filter(manufacturer_order_item__product_id=product_id)
    if batch_number:
        received = received.filter(code=batch_number)
    for batch in (
        received
        .select_related("manufacturer_order_item__order")
        .prefetch_related("manufacturer_order_item__order__items")
    ):
        item = batch.manufacturer_order_item
        cost = landed_unit_cost(item)
        if cost is not None:
            bought += cost * item.quantity
            bought_units += item.quantity

    paid_out = sum(
        ManufacturerOrderPayment.objects.filter(paid_at__gte=start, paid_at__lte=end)
        .values_list("amount", flat=True),
        ZERO,
    )
    paid_in = sum(
        PurchaseOrderPayment.objects.filter(
            status=PurchaseOrderPayment.Status.CONFIRMED,
            paid_at__gte=start,
            paid_at__lte=end,
        ).values_list("amount", flat=True),
        ZERO,
    )

    return PeriodReport(
        start=start,
        end=end,
        totals=totals,
        entries=entries,
        expenses=expenses,
        timeline=timeline,
        by_kind=_grouped(entries, lambda e: e.kind, lambda e: e.get_kind_display()),
        by_product=_grouped(
            entries,
            lambda e: (e.product_id, e.bottle_size),
            lambda e: f"{e.product.name}{_size_label(e)}",
            lambda e: e.product.sku,
        ),
        by_batch=_grouped(
            entries,
            lambda e: e.batch_number or "—",
            lambda e: e.batch_number or "No batch number",
            lambda e: f"{e.product.name}{_size_label(e)}",
        ),
        by_distributor=_grouped(
            [e for e in entries if e.distributor_profile_id],
            lambda e: e.distributor_profile_id,
            lambda e: e.distributor_profile.name,
        ),
        expenses_by_category=sorted(by_category.items(), key=lambda pair: pair[1], reverse=True),
        stock_bought=bought,
        stock_bought_units=bought_units,
        paid_to_manufacturers=paid_out,
        received_from_distributors=paid_in,
        filtered=filtered,
    )


@dataclass
class StockValue:
    units: Decimal = ZERO
    cost: Decimal = ZERO
    retail: Decimal = ZERO
    unknown_cost_units: Decimal = ZERO


def stock_on_hand_value():
    """What the stock still in the Owner's own warehouses (and not yet
    placed) is worth today, at cost and at retail. Stock in Transit is
    already sold to a Distributor, so it isn't counted."""
    from apps.owner_inventory.models import StockBatch
    from apps.owner_warehouse.models import Location

    value = StockValue()
    batches = (
        StockBatch.objects
        .filter(
            quantity_remaining__gt=0,
            location__location_type__in=[
                Location.LocationType.OWN,
                Location.LocationType.UNALLOCATED,
            ],
        )
        .select_related("product", "source_batch__manufacturer_order_item__order")
        .prefetch_related("source_batch__manufacturer_order_item__order__items")
    )
    for batch in batches:
        size = bottle_size_for(batch.product, batch)
        cost, source = unit_cost_for(batch.product, batch, size)
        value.units += batch.quantity_remaining
        value.cost += cost * batch.quantity_remaining
        value.retail += retail_price_for(batch.product, size) * batch.quantity_remaining
        if source == SaleEntry.CostSource.UNKNOWN:
            value.unknown_cost_units += batch.quantity_remaining
    return value
