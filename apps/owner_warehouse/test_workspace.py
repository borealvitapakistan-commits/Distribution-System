from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.owner_inventory.models import StockBatch, StockMovement
from apps.owner_inventory.services import get_or_create_unallocated_location, receive_stock, reallocate_batch
from apps.products.services import create_product

from .models import Inventory, Location
from .services import create_inventory, create_location, place_batch


class InventoryWorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(email="workspace@inventory.test", role=User.Role.OWNER)
        cls.product = create_product(actor=cls.owner, sku="UX-001", name="Vitamin C", base_retail_price=Decimal("500"), currency="PKR")
        cls.lahore = create_inventory(actor=cls.owner, code="LHR", name="Lahore")
        cls.islamabad = create_inventory(actor=cls.owner, code="ISB", name="Islamabad")
        cls.first = create_location(actor=cls.owner, code="JOHAR", name="Johar Town", location_type="OWN", inventory=cls.lahore)
        cls.second = create_location(actor=cls.owner, code="TOWN", name="Township", location_type="OWN", inventory=cls.lahore)
        cls.third = create_location(actor=cls.owner, code="ISB-WH", name="Islamabad Central", location_type="OWN", inventory=cls.islamabad)
        cls.pool = get_or_create_unallocated_location(actor=cls.owner)
        cls.regional_pool = get_or_create_unallocated_location(actor=cls.owner, inventory=cls.lahore)
        cls.batch = receive_stock(actor=cls.owner, product=cls.product, quantity=100, to_location=cls.pool,
                                  batch_number="LOT-100", expiry_date="2027-05-01", received_date="2026-10-22").batch
        cls.regional_batch = receive_stock(actor=cls.owner, product=cls.product, quantity=20,
                                           to_location=cls.regional_pool, batch_number="LOT-REGION").batch

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client.force_login(self.owner)

    def payload(self, *rows):
        data = {"placements-TOTAL_FORMS": str(len(rows)), "placements-INITIAL_FORMS": "0",
                "placements-MIN_NUM_FORMS": "1", "placements-MAX_NUM_FORMS": "25"}
        for index, (destination, quantity) in enumerate(rows):
            kind = "region" if isinstance(destination, Inventory) else "warehouse"
            data[f"placements-{index}-destination"] = f"{kind}:{destination.pk}"
            data[f"placements-{index}-quantity"] = str(quantity)
        return data

    def test_overview_is_read_only_and_counts_real_warehouses(self):
        before = Location.objects.count()
        response = self.client.get(reverse("inventory-list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Location.objects.count(), before)
        lahore = next(region for region in response.context["inventories"] if region.pk == self.lahore.pk)
        self.assertEqual(lahore.warehouse_count, 2)
        self.assertEqual(response.context["queue_count"], 2)
        self.assertEqual(response.context["queue_units"], Decimal("120"))
        self.assertContains(response, "Add region")
        self.assertContains(response, "Place stock")
        self.assertNotContains(response, "dest_qty_")

    def test_region_setup_only_needs_a_name_and_continues_to_warehouse(self):
        response = self.client.post(reverse("inventory-create"), {"name": "Karachi", "active": "on", "next": "warehouse"})
        region = Inventory.objects.get(name="Karachi")
        self.assertTrue(region.code.startswith("REG-KARACHI"))
        self.assertRedirects(response, f"{reverse('location-create')}?inventory={region.pk}")

    def test_warehouse_setup_can_skip_codes_and_capacity(self):
        response = self.client.post(reverse("location-create"), {
            "name": "DHA warehouse", "inventory": str(self.lahore.pk), "is_sellable": "on", "active": "on",
        })
        warehouse = Location.objects.get(name="DHA warehouse")
        self.assertTrue(warehouse.code.startswith("WH-DHA"))
        self.assertEqual(warehouse.capacity_units, 0)
        self.assertEqual(warehouse.area_square_feet, 0)
        self.assertRedirects(response, reverse("location-detail", args=[warehouse.pk]))

    def test_direct_split_across_warehouses_preserves_batch_details(self):
        response = self.client.post(reverse("inventory-allocate", args=[self.batch.pk]),
                                    self.payload((self.first, "65.25"), (self.third, "34.75")))
        self.assertEqual(response.status_code, 302)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity_remaining, 0)
        placed = StockBatch.objects.filter(batch_number="LOT-100").exclude(pk=self.batch.pk)
        self.assertEqual(placed.count(), 2)
        self.assertEqual(sum(batch.quantity_remaining for batch in placed), 100)
        self.assertTrue(all(str(batch.expiry_date) == "2027-05-01" for batch in placed))
        self.assertTrue(all(str(batch.received_date) == "2026-10-22" for batch in placed))

    def test_over_allocation_preserves_entries_and_moves_nothing(self):
        response = self.client.post(reverse("inventory-allocate", args=[self.batch.pk]),
                                    self.payload((self.first, "70"), (self.second, "50")))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "but this batch has only 100 units available")
        self.assertContains(response, 'value="70"')
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity_remaining, 100)
        self.assertFalse(StockBatch.objects.filter(location=self.first).exists())

    def test_legacy_over_allocation_is_also_atomic(self):
        islamabad_pool = get_or_create_unallocated_location(actor=self.owner, inventory=self.islamabad)
        response = self.client.post(reverse("inventory-allocate", args=[self.batch.pk]), {
            f"dest_qty_{self.regional_pool.pk}": "70", f"dest_qty_{islamabad_pool.pk}": "50",
        })
        self.assertEqual(response.status_code, 302)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity_remaining, 100)
        self.assertEqual(StockBatch.objects.filter(location=self.regional_pool).get().quantity_remaining, 20)

    def test_regional_stock_cannot_be_sent_to_a_different_region(self):
        response = self.client.post(reverse("inventory-allocate", args=[self.regional_batch.pk]), self.payload((self.third, 20)))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["formset"].errors)
        self.regional_batch.refresh_from_db()
        self.assertEqual(self.regional_batch.quantity_remaining, 20)

    def test_inactive_destination_is_rechecked_by_service(self):
        Location.objects.filter(pk=self.first.pk).update(active=False)
        with self.assertRaises(ValidationError):
            place_batch(actor=self.owner, batch_id=self.batch.pk, allocations=[(self.first, Decimal("100"))])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity_remaining, 100)

    def test_failed_second_move_rolls_back_the_first_move_and_ledger(self):
        movements_before = StockMovement.objects.count()
        calls = []

        def fail_second(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise ValidationError("Destination changed. Try again.")
            return reallocate_batch(**kwargs)

        with patch("apps.owner_inventory.services.reallocate_batch", side_effect=fail_second):
            with self.assertRaises(ValidationError):
                place_batch(actor=self.owner, batch_id=self.batch.pk,
                            allocations=[(self.first, Decimal("60")), (self.second, Decimal("40"))])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity_remaining, 100)
        self.assertEqual(StockMovement.objects.count(), movements_before)
        self.assertFalse(StockBatch.objects.filter(location=self.first).exists())

    def test_repeat_submission_cannot_move_the_same_units_twice(self):
        url = reverse("inventory-allocate", args=[self.batch.pk])
        payload = self.payload((self.first, 100))
        self.assertRedirects(self.client.post(url, payload), reverse("location-detail", args=[self.first.pk]))
        self.assertEqual(self.client.post(url, payload).status_code, 200)
        self.assertEqual(StockBatch.objects.get(location=self.first).quantity_remaining, 100)

    def test_region_only_placement_remains_available(self):
        response = self.client.post(reverse("inventory-allocate", args=[self.batch.pk]), self.payload((self.islamabad, 25)))
        self.assertRedirects(response, reverse("inventory-detail", args=[self.islamabad.pk]))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity_remaining, 75)

    def test_warehouse_context_prefills_destination_and_filters_available_batches(self):
        response = self.client.get(reverse("inventory-allocate", args=[self.batch.pk]), {"warehouse": str(self.first.pk)})
        self.assertEqual(response.context["formset"].forms[0]["destination"].value(), f"warehouse:{self.first.pk}")
        self.assertNotContains(response, f"warehouse:{self.third.pk}")
        self.assertEqual(self.client.get(reverse("location-create"), {"inventory": "bad-id"}).status_code, 200)

    def test_all_new_pages_and_form_errors_render(self):
        routes = [reverse("inventory-create"), reverse("location-create"),
                  reverse("inventory-allocate", args=[self.batch.pk]), reverse("inventory-add"),
                  reverse("stock-balance-list"), reverse("location-list")]
        for route in routes:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)
        self.assertEqual(self.client.post(reverse("inventory-create"), {"name": ""}).status_code, 200)
        self.assertEqual(self.client.post(reverse("location-create"), {"name": ""}).status_code, 200)

    def test_first_time_setup_explains_missing_regions_and_warehouses(self):
        Inventory.objects.update(active=False)
        response = self.client.get(reverse("location-create"))
        self.assertContains(response, "Add a region first")
        self.assertContains(response, f"{reverse('inventory-create')}?next=warehouse")
        response = self.client.get(reverse("inventory-add"))
        self.assertContains(response, "Add a warehouse first")
