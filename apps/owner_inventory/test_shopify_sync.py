from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.distributors.models import DistributorProfile
from apps.distributors.services import approve_distributor, create_distributor
from apps.owner_warehouse.models import Location
from apps.owner_warehouse.services import create_inventory, create_location, update_location
from apps.products import shopify
from apps.products.services import create_product

from . import shopify_sync
from .models import (
    ShopifyOrder,
    ShopifyOrderLine,
    ShopifySyncState,
    StockBalance,
    StockBatch,
    StockMovement,
)
from .services import (
    get_or_create_unallocated_location,
    give_to_distributor,
    receive_stock,
)

LOCATION_GID = "gid://shopify/Location/1"
ITEM_GID = "gid://shopify/InventoryItem/77"


def shopify_order(order_id="1001", quantity=3, product_id="555", cancelled=False, minutes_ago=0):
    return {
        "id": order_id,
        "name": f"#{order_id}",
        "created_at": (timezone.now() - timedelta(minutes=minutes_ago)).isoformat(),
        "cancelled": cancelled,
        "financial_status": "PAID",
        "lines": [
            {
                "id": f"line-{order_id}",
                "title": "Ashwagandha",
                "quantity": quantity,
                "product_id": product_id,
            }
        ],
    }


@override_settings(
    SHOPIFY_STORE_DOMAIN="test.myshopify.com",
    SHOPIFY_CLIENT_ID="id",
    SHOPIFY_CLIENT_SECRET="secret",
    SHOPIFY_LOCATION_ID="",
)
class ShopifySyncTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@shopifysync.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.product = create_product(
            actor=self.owner,
            sku="SYNC-001",
            barcode="",
            name="Ashwagandha",
            base_retail_price=Decimal("100.00"),
            currency="pkr",
        )
        self.product.shopify_product_id = "555"
        self.product.save(update_fields=["shopify_product_id"])

        region = create_inventory(actor=self.owner, code="SYNC-INV", name="Sync Region")
        self.warehouse_a = create_location(
            actor=self.owner, code="SYNC-A", name="Warehouse A",
            location_type=Location.LocationType.OWN, inventory=region,
        )
        self.warehouse_b = create_location(
            actor=self.owner, code="SYNC-B", name="Warehouse B",
            location_type=Location.LocationType.OWN, inventory=region,
        )

        # Soonest expiry is in B, so FEFO draws B first.
        receive_stock(
            actor=self.owner, product=self.product, quantity=Decimal("10"),
            to_location=self.warehouse_a, batch_number="LOT-A",
            expiry_date=date.today() + timedelta(days=300),
        )
        receive_stock(
            actor=self.owner, product=self.product, quantity=Decimal("2"),
            to_location=self.warehouse_b, batch_number="LOT-B",
            expiry_date=date.today() + timedelta(days=30),
        )

        self.pushed = {}
        self.orders = []
        patches = [
            mock.patch.object(shopify, "require_scopes"),
            mock.patch.object(shopify, "pick_location", return_value=(LOCATION_GID, "Shop location")),
            mock.patch.object(shopify, "inventory_item_for_product", return_value=(ITEM_GID, True, 1)),
            mock.patch.object(shopify, "track_inventory"),
            mock.patch.object(shopify, "fetch_available", return_value={}),
            mock.patch.object(shopify, "fetch_orders", side_effect=lambda since: list(self.orders)),
            mock.patch.object(
                shopify, "set_available",
                side_effect=lambda quantities, location_id, reference="": self.pushed.update(quantities),
            ),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _connect(self):
        """First sync: links the product and starts the order clock."""
        shopify_sync.sync(actor=self.owner)

    def test_first_sync_pushes_sellable_total_and_imports_no_history(self):
        self.orders = [shopify_order(minutes_ago=60 * 24)]

        self._connect()

        self.assertEqual(self.pushed, {ITEM_GID: 12})
        self.assertFalse(ShopifyOrder.objects.exists())
        self.assertIsNotNone(ShopifySyncState.load().orders_since)
        self.product.refresh_from_db()
        self.assertEqual(self.product.shopify_inventory_item_id, ITEM_GID)

    def test_shopify_order_takes_stock_fefo_and_is_traceable(self):
        self._connect()
        self.orders = [shopify_order(quantity=3)]

        summary = shopify_sync.sync(actor=self.owner)

        self.assertEqual(summary["lines_posted"], 1)
        line = ShopifyOrderLine.objects.get()
        self.assertEqual(line.status, ShopifyOrderLine.Status.POSTED)
        self.assertEqual(line.product, self.product)

        sales = StockMovement.objects.filter(
            movement_type=StockMovement.MovementType.SALE,
            channel=StockMovement.Channel.SHOPIFY,
        ).order_by("created_at")
        self.assertEqual(
            [(m.batch.batch_number, m.quantity) for m in sales],
            [("LOT-B", Decimal("2")), ("LOT-A", Decimal("1"))],
        )
        self.assertTrue(all(m.reference == "Shopify order #1001" for m in sales))

        self.assertEqual(StockBatch.objects.get(batch_number="LOT-A").quantity_remaining, Decimal("9"))
        self.assertEqual(self.pushed, {ITEM_GID: 9})

    def test_same_order_is_never_imported_twice(self):
        self._connect()
        self.orders = [shopify_order(quantity=1)]

        shopify_sync.sync(actor=self.owner)
        shopify_sync.sync(actor=self.owner)

        self.assertEqual(ShopifyOrder.objects.count(), 1)
        self.assertEqual(
            StockMovement.objects.filter(movement_type=StockMovement.MovementType.SALE).count(), 1
        )

    def test_short_line_waits_and_posts_once_stock_arrives(self):
        self._connect()
        self.orders = [shopify_order(quantity=20)]

        shopify_sync.sync(actor=self.owner)

        line = ShopifyOrderLine.objects.get()
        self.assertEqual(line.status, ShopifyOrderLine.Status.SHORT)
        self.assertFalse(StockMovement.objects.filter(movement_type="SALE").exists())

        receive_stock(
            actor=self.owner, product=self.product, quantity=Decimal("10"),
            to_location=self.warehouse_a,
        )
        shopify_sync.sync(actor=self.owner)

        line.refresh_from_db()
        self.assertEqual(line.status, ShopifyOrderLine.Status.POSTED)
        self.assertEqual(self.pushed, {ITEM_GID: 2})

    def test_cancelled_order_is_recorded_without_taking_stock(self):
        self._connect()
        self.orders = [shopify_order(cancelled=True)]

        shopify_sync.sync(actor=self.owner)

        self.assertEqual(ShopifyOrderLine.objects.get().status, ShopifyOrderLine.Status.SKIPPED)
        self.assertEqual(self.pushed, {ITEM_GID: 12})

    def test_unlinked_product_is_flagged(self):
        self._connect()
        self.orders = [shopify_order(product_id="999")]

        shopify_sync.sync(actor=self.owner)

        self.assertEqual(ShopifyOrderLine.objects.get().status, ShopifyOrderLine.Status.UNLINKED)

    def test_unallocated_and_held_back_stock_is_not_offered_on_shopify(self):
        receive_stock(
            actor=self.owner, product=self.product, quantity=Decimal("50"),
            to_location=get_or_create_unallocated_location(actor=self.owner),
        )
        update_location(actor=self.owner, location_id=self.warehouse_b.pk, is_sellable=False)

        self._connect()

        self.assertEqual(self.pushed, {ITEM_GID: 10})

    @override_settings(SHOPIFY_INVENTORY_SYNC=True)
    def test_distributor_sale_updates_shopify_after_commit(self):
        self._connect()
        distributor_user = create_distributor(
            actor=self.owner,
            email="dist@shopifysync.test",
            temporary_password="TemporaryPassword123!",
            name="Sync Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=distributor_user.pk)
        profile = DistributorProfile.objects.get(user=distributor_user)
        batch = StockBatch.objects.get(batch_number="LOT-A")

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            give_to_distributor(
                actor=self.owner, product=self.product, quantity=Decimal("4"),
                from_location=self.warehouse_a, distributor_profile=profile, batch=batch,
            )

        self.assertEqual(len(callbacks), 1)
        self.assertEqual(self.pushed, {ITEM_GID: 8})
        self.assertEqual(
            StockMovement.objects.filter(channel=StockMovement.Channel.DISTRIBUTOR).count(), 1
        )

    @override_settings(SHOPIFY_INVENTORY_SYNC=True)
    def test_shopify_outage_never_blocks_a_stock_change(self):
        with mock.patch.object(shopify, "pick_location", side_effect=shopify.ShopifyError("down")):
            with self.captureOnCommitCallbacks(execute=True):
                receive_stock(
                    actor=self.owner, product=self.product, quantity=Decimal("5"),
                    to_location=self.warehouse_a,
                )

        self.assertEqual(
            StockBalance.objects.get(product=self.product, location=self.warehouse_a).quantity,
            Decimal("15"),
        )
        self.assertEqual(ShopifySyncState.load().last_error, "down")

    def test_sync_page_renders_for_owner(self):
        self._connect()
        self.client.force_login(self.owner)

        with mock.patch.object(shopify, "fetch_available", return_value={ITEM_GID: 12}):
            response = self.client.get("/owner/inventory/shopify/")

        self.assertContains(response, "In step")


@override_settings(
    SHOPIFY_STORE_DOMAIN="test.myshopify.com",
    SHOPIFY_CLIENT_ID="id",
    SHOPIFY_CLIENT_SECRET="secret",
    SHOPIFY_LOCATION_ID="",
    SHOPIFY_INVENTORY_SYNC=True,
)
class WhiteboardFlowTests(TestCase):
    """Purchase Order → Manufacturer → Items → Inventory → Warehouses, and
    that one Inventory feeding both the Software (Distributors) and
    Shopify — checked end to end through the real services."""

    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@flow.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.product = create_product(
            actor=self.owner,
            sku="FLOW-001",
            barcode="",
            name="Allergy Relief",
            base_retail_price=Decimal("100.00"),
            currency="pkr",
        )
        self.product.shopify_product_id = "555"
        self.product.save(update_fields=["shopify_product_id"])

        self.pushed = []
        self.orders = []
        patches = [
            mock.patch.object(shopify, "require_scopes"),
            mock.patch.object(shopify, "pick_location", return_value=(LOCATION_GID, "Shop location")),
            mock.patch.object(shopify, "inventory_item_for_product", return_value=(ITEM_GID, True, 1)),
            mock.patch.object(shopify, "track_inventory"),
            mock.patch.object(shopify, "fetch_available", return_value={}),
            mock.patch.object(shopify, "fetch_orders", side_effect=lambda since: list(self.orders)),
            mock.patch.object(
                shopify, "set_available",
                side_effect=lambda quantities, location_id, reference="": self.pushed.append(
                    quantities[ITEM_GID]
                ),
            ),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

        # Connecting the store for the first time (nothing in stock yet).
        shopify_sync.sync(actor=self.owner)

    @contextmanager
    def saved(self):
        """Runs the on-commit callbacks like a real save does, then clears
        them — inside a TestCase they'd otherwise stay queued, since the
        test's own transaction never commits."""
        start = len(connection.run_on_commit)
        with self.captureOnCommitCallbacks(execute=True):
            yield
        del connection.run_on_commit[start:]

    def shopify_shows(self):
        return self.pushed[-1]

    def software_total(self):
        return sum(
            StockBalance.objects.filter(product=self.product).values_list("quantity", flat=True),
            Decimal("0"),
        )

    def test_full_flow(self):
        from apps.batches.services import trace_batch_code
        from apps.manufacturers.services import (
            create_manufacturer,
            create_manufacturer_order,
            mark_manufacturer_order_received,
            record_manufacturer_invoice,
        )
        from apps.requests.services import (
            create_purchase_order,
            receive_purchase_order_item,
            ship_purchase_order_item,
        )

        from .services import reallocate_batch, unallocated_batches

        self.assertEqual(self.shopify_shows(), 0)

        # 1. Purchase Order → Manufacturer
        manufacturer = create_manufacturer(actor=self.owner, name="Flow Labs")
        order = create_manufacturer_order(
            actor=self.owner,
            manufacturer=manufacturer,
            items=[{"product": self.product, "quantity": Decimal("100"), "unit_price": Decimal("50")}],
        )
        item = order.items.get()
        batch_code = item.batch.code

        # 2. Manufacturer sends the items; invoice approved → Inventory
        mark_manufacturer_order_received(actor=self.owner, order_id=order.pk)
        with self.saved():
            record_manufacturer_invoice(actor=self.owner, order_id=order.pk)

        pool_batch = unallocated_batches(product=self.product).get()
        self.assertEqual(pool_batch.quantity_remaining, Decimal("100"))
        self.assertEqual(pool_batch.batch_number, batch_code)
        self.assertEqual(self.software_total(), Decimal("100"))
        # Not placed in a warehouse yet, so not offered online yet.
        self.assertEqual(self.shopify_shows(), 0)

        # 3. Inventory → Warehouses (region, then warehouse)
        region = create_inventory(actor=self.owner, code="LHR", name="Lahore")
        warehouse = create_location(
            actor=self.owner, code="LHR-1", name="Johar Town",
            location_type=Location.LocationType.OWN, inventory=region,
        )
        with self.saved():
            region_batch = reallocate_batch(
                actor=self.owner, batch=pool_batch, quantity=Decimal("100"),
                destination_location=get_or_create_unallocated_location(
                    actor=self.owner, inventory=region
                ),
            )
        with self.saved():
            warehouse_batch = reallocate_batch(
                actor=self.owner, batch=region_batch, quantity=Decimal("100"),
                destination_location=warehouse,
            )

        # Software and Shopify now show the same number.
        self.assertEqual(self.software_total(), Decimal("100"))
        self.assertEqual(self.shopify_shows(), 100)

        # 4a. Inventory → Software: Majid buys 30 and receives them;
        #     ABC buys 10, still on the way.
        def distributor(name, email):
            user = create_distributor(
                actor=self.owner, email=email,
                temporary_password="TemporaryPassword123!", name=name,
            )
            approve_distributor(user=self.owner, distributor_id=user.pk)
            user.refresh_from_db()
            return user

        def ship(user, quantity):
            po = create_purchase_order(
                actor=user,
                items=[{"product": self.product, "quantity_requested": quantity}],
            )
            po_item = po.items.get()
            with self.saved():
                ship_purchase_order_item(
                    actor=self.owner, item_id=po_item.pk,
                    allocations=[(warehouse_batch, quantity)],
                )
            return po_item

        majid = distributor("Majid", "majid@flow.test")
        majid_item = ship(majid, Decimal("30"))
        self.assertEqual(self.shopify_shows(), 70)
        with self.saved():
            receive_purchase_order_item(actor=majid, item_id=majid_item.pk, quantity=Decimal("30"))

        abc = distributor("ABC", "abc@flow.test")
        ship(abc, Decimal("10"))
        self.assertEqual(self.shopify_shows(), 60)

        # 4b. Inventory → Shopify: an online customer buys 2
        self.orders = [shopify_order(order_id="2001", quantity=2, product_id="555")]
        shopify_sync.sync(actor=self.owner)

        self.assertEqual(self.shopify_shows(), 58)
        self.assertEqual(
            StockBalance.objects.get(product=self.product, location=warehouse).quantity,
            Decimal("58"),
        )

        # 5. Tracing the batch: manufacturer → warehouse/inventory →
        #    each distributor by name → each Shopify order.
        trace = trace_batch_code(batch_code)
        steps = [(e.stage, e.from_label, e.to_label, e.quantity) for e in trace["events"]]

        self.assertIn(("Owner → Manufacturer (ordered)", "Owner", "Flow Labs", Decimal("100")), steps)
        self.assertIn(("Manufacturer → Inventory (received)", "Flow Labs", "Unallocated", Decimal("100")), steps)
        self.assertIn(
            ("Inventory → Warehouse (stored)", "Unallocated — Lahore", "Johar Town (Lahore)", Decimal("100")),
            steps,
        )
        self.assertIn(
            ("Inventory → Distributor (shipped)", "Johar Town (Lahore)", "Majid (in transit)", Decimal("30")),
            steps,
        )
        self.assertIn(
            ("Inventory → Distributor (shipped)", "Johar Town (Lahore)", "ABC (in transit)", Decimal("10")),
            steps,
        )
        self.assertIn(
            ("Inventory → Shopify (sold)", "Johar Town (Lahore)", "Shopify order #2001", Decimal("2")),
            steps,
        )

        totals = trace["totals"]
        self.assertEqual(totals["ordered"], Decimal("100"))
        self.assertEqual(totals["received_by_owner"], Decimal("100"))
        self.assertEqual(totals["sold_on_shopify"], Decimal("2"))
        self.assertEqual(totals["in_transit"], Decimal("10"))
        self.assertEqual(totals["received_by_distributors"], Decimal("30"))
        self.assertEqual(totals["owner_on_hand"], Decimal("58"))
        self.assertEqual(trace["where_now"], [("Johar Town (Lahore)", Decimal("58"))])
        self.assertEqual(
            {row.name: (row.shipped, row.received) for row in trace["distributors"]},
            {"Majid": (Decimal("30"), Decimal("30")), "ABC": (Decimal("10"), Decimal("0"))},
        )

        # 6. Every action is in the audit log.
        from apps.audit.models import AuditEvent

        actions = set(AuditEvent.objects.values_list("action", flat=True))
        for action in (
            "manufacturer.order_invoice_recorded",
            "inventory.stock_movement_posted",
            "requests.purchase_order_item_shipped",
            "inventory.shopify_order_imported",
            "inventory.shopify_quantities_pushed",
        ):
            self.assertIn(action, actions)


@override_settings(
    SHOPIFY_STORE_DOMAIN="test.myshopify.com",
    SHOPIFY_CLIENT_ID="id",
    SHOPIFY_CLIENT_SECRET="secret",
    SHOPIFY_LOCATION_ID="",
)
class PublishProductsTests(TestCase):
    """Sync puts every product with stock on Shopify, not only the ones
    that were imported from it."""

    ALL_SCOPES = set(shopify.INVENTORY_SYNC_SCOPES) | set(shopify.PRODUCT_PUBLISH_SCOPES)

    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@publish.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        region = create_inventory(actor=self.owner, code="PUB-INV", name="Lahore")
        self.warehouse = create_location(
            actor=self.owner, code="PUB-WH", name="Johar Town",
            location_type=Location.LocationType.OWN, inventory=region,
        )
        self.omega = create_product(
            actor=self.owner, sku="BV-OMEGA3-60", barcode="8961234567890",
            name="Omega-3 Fish Oil Softgels (60ct)", base_retail_price=Decimal("1850.00"),
            currency="pkr",
        )
        self.omega.compare_at_price = Decimal("2100.00")
        self.omega.weight_grams = Decimal("120")
        self.omega.tags = "omega, heart"
        self.omega.save()
        receive_stock(
            actor=self.owner, product=self.omega, quantity=Decimal("40"),
            to_location=self.warehouse,
        )
        # No stock — nothing to sell, so not created.
        create_product(
            actor=self.owner, sku="BV-EMPTY", barcode="", name="Empty Product",
            base_retail_price=Decimal("10"), currency="pkr",
        )

        self.scopes = set(self.ALL_SCOPES)
        self.created = []
        self.shopify_products = []
        self.pushed = {}
        patches = [
            mock.patch.object(shopify, "require_scopes", side_effect=lambda *needed: self.scopes),
            mock.patch.object(shopify, "pick_location", return_value=(LOCATION_GID, "Shop location")),
            mock.patch.object(shopify, "inventory_item_for_product", return_value=(ITEM_GID, True, 1)),
            mock.patch.object(shopify, "fetch_available", return_value={}),
            mock.patch.object(shopify, "fetch_orders", return_value=[]),
            mock.patch.object(shopify, "list_products", side_effect=lambda: list(self.shopify_products)),
            mock.patch.object(shopify, "online_store_publication_id", return_value="gid://shopify/Publication/9"),
            mock.patch.object(shopify, "publish"),
            mock.patch.object(
                shopify, "create_product",
                side_effect=lambda data: self.created.append(data) or (
                    "900", "gid://shopify/Product/900", "gid://shopify/InventoryItem/901"
                ),
            ),
            mock.patch.object(
                shopify, "set_available",
                side_effect=lambda quantities, location_id, reference="": self.pushed.update(quantities),
            ),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_product_with_stock_is_created_with_shopify_fields_and_quantity(self):
        summary = shopify_sync.sync(actor=self.owner)

        self.assertEqual(summary["products_created"], 1)
        self.assertEqual(len(self.created), 1)
        data = self.created[0]
        self.assertEqual(data["title"], "Omega-3 Fish Oil Softgels (60ct)")
        self.assertEqual(data["status"], "ACTIVE")
        self.assertEqual(data["tags"], ["omega", "heart"])
        variant = data["variants"][0]
        self.assertEqual(variant["sku"], "BV-OMEGA3-60")
        self.assertEqual(variant["barcode"], "8961234567890")
        self.assertEqual(variant["price"], "1850.00")
        self.assertEqual(variant["compareAtPrice"], "2100.00")
        self.assertEqual(variant["inventoryItem"]["measurement"]["weight"], {"unit": "GRAMS", "value": 120.0})
        self.assertEqual(
            variant["inventoryQuantities"],
            [{"locationId": LOCATION_GID, "name": "available", "quantity": 40}],
        )
        shopify.publish.assert_called_once_with("gid://shopify/Product/900", "gid://shopify/Publication/9")

        self.omega.refresh_from_db()
        self.assertEqual(self.omega.shopify_product_id, "900")
        self.assertEqual(self.pushed, {"gid://shopify/InventoryItem/901": 40})

        # Already on Shopify now — the next sync doesn't create it again.
        shopify_sync.sync(actor=self.owner)
        self.assertEqual(len(self.created), 1)

    def test_same_sku_on_shopify_is_linked_not_duplicated(self):
        self.shopify_products = [
            {"id": "555", "title": "Omega 3", "handle": "omega-3", "skus": ["BV-OMEGA3-60"]}
        ]

        summary = shopify_sync.sync(actor=self.owner)

        self.assertEqual(summary["products_linked"], 1)
        self.assertEqual(self.created, [])
        self.omega.refresh_from_db()
        self.assertEqual(self.omega.shopify_product_id, "555")

    def test_without_product_scopes_quantities_still_sync(self):
        self.scopes = set(shopify.INVENTORY_SYNC_SCOPES)

        summary = shopify_sync.sync(actor=self.owner)

        self.assertEqual(self.created, [])
        self.assertTrue(any("write_products" in warning for warning in summary["warnings"]))

    def test_linking_by_hand_moves_the_link_off_the_empty_copy(self):
        imported = create_product(
            actor=self.owner, sku="SHOPIFY-555", barcode="", name="Omega 3 (imported)",
            base_retail_price=Decimal("1850"), currency="pkr",
        )
        imported.shopify_product_id = "555"
        imported.shopify_inventory_item_id = ITEM_GID
        imported.save()

        self.client.force_login(self.owner)
        self.client.post(
            "/owner/inventory/shopify/",
            {"action": "link", "product": self.omega.pk, "shopify_product": "555"},
        )

        self.omega.refresh_from_db()
        imported.refresh_from_db()
        self.assertEqual(self.omega.shopify_product_id, "555")
        self.assertEqual(imported.shopify_product_id, "")

        shopify_sync.sync(actor=self.owner)
        self.assertEqual(self.created, [])
        self.assertEqual(self.pushed, {ITEM_GID: 40})


@override_settings(
    SHOPIFY_STORE_DOMAIN="test.myshopify.com",
    SHOPIFY_CLIENT_ID="id",
    SHOPIFY_CLIENT_SECRET="secret",
    SHOPIFY_LOCATION_ID="",
)
class ManualShopifyChangeTests(TestCase):
    """A quantity typed into Shopify's Inventory screen changes the
    software's stock too — recorded as an adjustment, not lost."""

    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@manual.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.product = create_product(
            actor=self.owner, sku="BV-PROB-20B", barcode="", name="Probiotic",
            base_retail_price=Decimal("2100"), currency="pkr",
        )
        self.product.shopify_product_id = "555"
        self.product.save(update_fields=["shopify_product_id"])
        region = create_inventory(actor=self.owner, code="MAN-INV", name="Islamabad")
        self.warehouse = create_location(
            actor=self.owner, code="MAN-WH", name="Faizaabad",
            location_type=Location.LocationType.OWN, inventory=region,
        )
        receive_stock(
            actor=self.owner, product=self.product, quantity=Decimal("200"),
            to_location=self.warehouse, batch_number="BOR-PRO-001",
        )

        # A tiny fake Shopify: holds the available count, lowered by orders.
        self.shopify_count = None
        self.orders = []

        def set_available(quantities, location_id, reference=""):
            self.shopify_count = quantities[ITEM_GID]

        patches = [
            mock.patch.object(shopify, "require_scopes", return_value=set(shopify.INVENTORY_SYNC_SCOPES)),
            mock.patch.object(shopify, "pick_location", return_value=(LOCATION_GID, "Shop location")),
            mock.patch.object(shopify, "inventory_item_for_product", return_value=(ITEM_GID, True, 1)),
            mock.patch.object(
                shopify, "fetch_available",
                side_effect=lambda ids, location_id: {ITEM_GID: self.shopify_count},
            ),
            mock.patch.object(shopify, "fetch_orders", side_effect=lambda since: list(self.orders)),
            mock.patch.object(shopify, "set_available", side_effect=set_available),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

        shopify_sync.sync(actor=self.owner)
        self.assertEqual(self.shopify_count, 200)

    def stock(self):
        return StockBalance.objects.get(product=self.product, location=self.warehouse).quantity

    def test_lowered_on_shopify_lowers_the_software(self):
        self.shopify_count = 100  # typed in on Shopify

        summary = shopify_sync.sync(actor=self.owner)

        self.assertEqual(self.stock(), Decimal("100"))
        self.assertEqual(self.shopify_count, 100)
        movement = StockMovement.objects.get(
            movement_type=StockMovement.MovementType.ADJUSTMENT,
            channel=StockMovement.Channel.SHOPIFY,
        )
        self.assertEqual(movement.quantity, Decimal("100"))
        self.assertEqual(movement.batch.batch_number, "BOR-PRO-001")
        self.assertIn("200 → 100", movement.reference)
        self.assertEqual(summary["manual_changes"], ["Probiotic: -100 taken out of stock"])

        from apps.batches.services import trace_batch_code

        stages = [event.stage for event in trace_batch_code("BOR-PRO-001")["events"]]
        self.assertIn("Inventory (changed on Shopify)", stages)

        # Nothing changed since — the next sync records nothing new.
        shopify_sync.sync(actor=self.owner)
        self.assertEqual(self.stock(), Decimal("100"))

    def test_raised_on_shopify_raises_the_software(self):
        self.shopify_count = 250

        shopify_sync.sync(actor=self.owner)

        self.assertEqual(self.stock(), Decimal("250"))
        self.assertEqual(self.shopify_count, 250)

    def test_an_order_is_a_sale_not_a_manual_change(self):
        self.orders = [shopify_order(order_id="3001", quantity=5, product_id="555")]
        self.shopify_count = 195  # Shopify took the 5 off for the order

        summary = shopify_sync.sync(actor=self.owner)

        self.assertEqual(summary["manual_changes"], [])
        self.assertEqual(self.stock(), Decimal("195"))
        self.assertFalse(
            StockMovement.objects.filter(movement_type=StockMovement.MovementType.ADJUSTMENT).exists()
        )

    def test_order_and_manual_change_together(self):
        self.orders = [shopify_order(order_id="3002", quantity=5, product_id="555")]
        self.shopify_count = 195 - 45  # the order, then 45 removed by hand

        shopify_sync.sync(actor=self.owner)

        self.assertEqual(self.stock(), Decimal("150"))
        self.assertEqual(self.shopify_count, 150)
