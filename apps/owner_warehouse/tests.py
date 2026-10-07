from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import Client, TestCase

from apps.accounts.models import User
from apps.distributors.services import approve_distributor, create_distributor
from apps.owner_inventory.models import StockBatch
from apps.owner_inventory.services import (
    get_or_create_unallocated_location,
    reallocate_batch,
    receive_stock,
    unallocated_batches,
)
from apps.manufacturers.services import create_manufacturer
from apps.products.services import create_product

from .forms import LocationForm
from .models import Inventory, Location
from .services import create_inventory, create_location, update_location


class WarehouseRulesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@warehouse.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.manufacturer = create_manufacturer(
            actor=self.owner,
            name="Warehouse Manufacturer",
        )
        self.inventory = create_inventory(
            actor=self.owner, code="WH-TEST-INV", name="Test Region"
        )

    def test_location_truth_table(self):
        cases = [
            ("OWN", {"inventory": self.inventory}, (True, True, True)),
            ("SUPPLIER", {"manufacturer": self.manufacturer}, (False, False, False)),
        ]

        for index, (location_type, links, expected) in enumerate(cases):
            location = create_location(
                actor=self.owner,
                code=f"LOC-{index}",
                name=f"Location {index}",
                location_type=location_type,
                **links,
            )
            self.assertEqual(
                (location.on_book, location.is_physical, location.is_sellable),
                expected,
            )

    def test_location_link_must_match_type(self):
        with self.assertRaises(ValidationError):
            create_location(
                actor=self.owner,
                code="BAD-OWN",
                name="Owned Location With Manufacturer",
                location_type=Location.LocationType.OWN,
                inventory=self.inventory,
                manufacturer=self.manufacturer,
            )

        with self.assertRaises(ValidationError):
            create_location(
                actor=self.owner,
                code="MISSING-SUPPLIER",
                name="Missing Manufacturer Link",
                location_type=Location.LocationType.SUPPLIER,
            )

    def test_own_location_requires_inventory(self):
        with self.assertRaises(ValidationError):
            create_location(
                actor=self.owner,
                code="NO-INV",
                name="No Region Warehouse",
                location_type=Location.LocationType.OWN,
            )

    def test_owned_warehouse_sellable_is_the_owners_choice(self):
        default = create_location(
            actor=self.owner,
            code="SELL-DEFAULT",
            name="Default Warehouse",
            location_type=Location.LocationType.OWN,
            inventory=self.inventory,
        )
        self.assertTrue(default.is_sellable)

        held = create_location(
            actor=self.owner,
            code="SELL-HELD",
            name="Held Warehouse",
            location_type=Location.LocationType.OWN,
            inventory=self.inventory,
            is_sellable=False,
        )
        self.assertFalse(held.is_sellable)

        released = update_location(
            actor=self.owner, location_id=held.pk, is_sellable=True
        )
        self.assertTrue(released.is_sellable)

    def test_supplier_location_is_never_sellable(self):
        location = create_location(
            actor=self.owner,
            code="SUP-SELL",
            name="Supplier",
            location_type=Location.LocationType.SUPPLIER,
            manufacturer=self.manufacturer,
            is_sellable=True,
        )
        self.assertFalse(location.is_sellable)

    def test_non_own_location_cannot_have_inventory(self):
        with self.assertRaises(ValidationError):
            create_location(
                actor=self.owner,
                code="BAD-SHOPIFY",
                name="Shopify With Region",
                location_type=Location.LocationType.SHOPIFY,
                inventory=self.inventory,
            )


class InventoryRulesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@inventoryregion.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )

    def test_inventory_code_is_unique(self):
        create_inventory(actor=self.owner, code="LHR", name="Lahore")

        with self.assertRaises(ValidationError):
            create_inventory(actor=self.owner, code="LHR", name="Lahore Again")

    def test_inventory_pages_are_connected(self):
        inventory = create_inventory(actor=self.owner, code="ISB", name="Islamabad")
        self.client.force_login(self.owner)

        for route in [
            "/owner/inventory/",
            "/owner/inventory/new/",
            f"/owner/inventory/{inventory.pk}/",
            f"/owner/inventory/{inventory.pk}/edit/",
        ]:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)

    def test_distributor_cannot_access_inventory_pages(self):
        distributor_user = create_distributor(
            actor=self.owner,
            email="dist@inventoryregion.test",
            temporary_password="TemporaryPassword123!",
            name="Inventory Region Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=distributor_user.pk)
        distributor_user.refresh_from_db()
        self.client.force_login(distributor_user)

        response = self.client.get("/owner/inventory/")
        self.assertEqual(response.status_code, 403)


class LocationFormWiringTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@warehouseform.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.inventory = create_inventory(
            actor=self.owner, code="FORM-INV", name="Form Region"
        )

    def test_form_only_offers_own_and_shopify(self):
        offered = {choice for choice, _ in LocationForm.MANUAL_LOCATION_TYPES}
        self.assertEqual(offered, {"OWN", "SHOPIFY"})

    def test_create_view_does_not_accept_distributor_link(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get("/owner/warehouse/locations/new/")
        self.assertNotContains(response, 'name="manufacturer"')

        response = client.post(
            "/owner/warehouse/locations/new/",
            {
                "code": "MANUAL-WH",
                "name": "Manual Warehouse",
                "location_type": "OWN",
                "inventory": str(self.inventory.pk),
                "area_square_feet": "0",
                "capacity_units": "0",
                "active": "on",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        location = Location.objects.get(code="MANUAL-WH")
        self.assertEqual(location.location_type, "OWN")
        self.assertEqual(location.inventory_id, self.inventory.pk)

    def test_cannot_edit_system_managed_location(self):
        location = create_location(
            actor=self.owner,
            code="AUTO-TRANSIT",
            name="Auto Transit Location",
            location_type=Location.LocationType.TRANSIT,
        )

        client = Client()
        client.force_login(self.owner)

        response = client.get(
            f"/owner/warehouse/locations/{location.pk}/edit/", follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "managed automatically")

        response = client.post(
            f"/owner/warehouse/locations/{location.pk}/edit/",
            {
                "code": "AUTO-TRANSIT",
                "name": "Renamed",
                "location_type": "OWN",
                "area_square_feet": "0",
                "capacity_units": "0",
                "active": "on",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        location.refresh_from_db()
        self.assertEqual(location.location_type, "TRANSIT")


class AllocationFlowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@allocate.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.product = create_product(
            actor=self.owner,
            sku="ALLOC-001",
            barcode="",
            name="Allocate Product",
            base_retail_price=Decimal("10.00"),
            currency="pkr",
        )
        self.lahore = create_inventory(actor=self.owner, code="LHR", name="Lahore")
        self.islamabad = create_inventory(actor=self.owner, code="ISB", name="Islamabad")
        self.lahore_wh1 = create_location(
            actor=self.owner,
            code="LHR-WH1",
            name="Lahore Warehouse 1",
            location_type=Location.LocationType.OWN,
            inventory=self.lahore,
        )
        self.lahore_wh2 = create_location(
            actor=self.owner,
            code="LHR-WH2",
            name="Lahore Warehouse 2",
            location_type=Location.LocationType.OWN,
            inventory=self.lahore,
        )

    def test_global_unallocated_stock_can_be_split_across_regions(self):
        global_pool = get_or_create_unallocated_location(actor=self.owner)
        movement = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("1000"),
            to_location=global_pool,
        )
        batch = movement.batch

        self.assertEqual(unallocated_batches().count(), 1)

        lahore_pool = get_or_create_unallocated_location(
            actor=self.owner, inventory=self.lahore
        )
        islamabad_pool = get_or_create_unallocated_location(
            actor=self.owner, inventory=self.islamabad
        )

        reallocate_batch(
            actor=self.owner, batch=batch, quantity=Decimal("600"),
            destination_location=lahore_pool,
        )
        reallocate_batch(
            actor=self.owner, batch=batch, quantity=Decimal("400"),
            destination_location=islamabad_pool,
        )

        batch.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, Decimal("0"))
        self.assertEqual(
            unallocated_batches(inventory=self.lahore).first().quantity_remaining,
            Decimal("600"),
        )
        self.assertEqual(
            unallocated_batches(inventory=self.islamabad).first().quantity_remaining,
            Decimal("400"),
        )

    def test_regional_unallocated_stock_can_be_split_across_warehouses(self):
        lahore_pool = get_or_create_unallocated_location(
            actor=self.owner, inventory=self.lahore
        )
        movement = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("100"),
            to_location=lahore_pool,
            expiry_date="2027-01-01",
            batch_number="BV-GL-AML-001",
        )
        batch = movement.batch

        reallocate_batch(
            actor=self.owner, batch=batch, quantity=Decimal("70"),
            destination_location=self.lahore_wh1,
        )
        reallocate_batch(
            actor=self.owner, batch=batch, quantity=Decimal("30"),
            destination_location=self.lahore_wh2,
        )

        wh1_batch = StockBatch.objects.get(location=self.lahore_wh1)
        wh2_batch = StockBatch.objects.get(location=self.lahore_wh2)

        self.assertEqual(wh1_batch.quantity_remaining, Decimal("70"))
        self.assertEqual(wh2_batch.quantity_remaining, Decimal("30"))
        # Expiry and batch number survive the move — they don't reset or
        # go missing just because the stock changed rooms, even though
        # this split into two separate rows.
        self.assertEqual(str(wh1_batch.expiry_date), "2027-01-01")
        self.assertEqual(str(wh2_batch.expiry_date), "2027-01-01")
        self.assertEqual(wh1_batch.batch_number, "BV-GL-AML-001")
        self.assertEqual(wh2_batch.batch_number, "BV-GL-AML-001")

    def test_cannot_allocate_from_a_non_unallocated_location(self):
        movement = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("10"),
            to_location=self.lahore_wh1,
        )

        with self.assertRaises(ValidationError):
            reallocate_batch(
                actor=self.owner,
                batch=movement.batch,
                quantity=Decimal("5"),
                destination_location=self.lahore_wh2,
            )

    def test_allocate_view_moves_stock_from_global_pool_to_region(self):
        global_pool = get_or_create_unallocated_location(actor=self.owner)
        movement = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("50"),
            to_location=global_pool,
        )

        client = Client()
        client.force_login(self.owner)

        lahore_pool = get_or_create_unallocated_location(
            actor=self.owner, inventory=self.lahore
        )

        response = client.post(
            f"/owner/inventory/allocate/{movement.batch.pk}/",
            {f"dest_qty_{lahore_pool.pk}": "50", "return_to": "inventory-list"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        self.assertEqual(unallocated_batches(inventory=self.lahore).count(), 1)
        self.assertEqual(unallocated_batches().count(), 0)

    def test_warehouse_detail_page_shows_per_product_breakdown(self):
        other_product = create_product(
            actor=self.owner,
            sku="ALLOC-002",
            barcode="",
            name="Second Product",
            base_retail_price=Decimal("5.00"),
            currency="pkr",
        )
        receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("10"),
            to_location=self.lahore_wh1,
        )
        receive_stock(
            actor=self.owner,
            product=other_product,
            quantity=Decimal("25"),
            to_location=self.lahore_wh1,
        )

        client = Client()
        client.force_login(self.owner)

        response = client.get(f"/owner/warehouse/locations/{self.lahore_wh1.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Allocate Product")
        self.assertContains(response, "10")
        self.assertContains(response, "Second Product")
        self.assertContains(response, "25")
