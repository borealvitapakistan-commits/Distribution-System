from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.products.models import Product, ProductPackagePrice
from apps.products.services import create_product

from .deletion import delete_manufacturer, review_manufacturer
from .models import Manufacturer, ManufacturerOrder
from .services import create_manufacturer


class ManufacturerResolutionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="resolve@test.local", role="OWNER")
        self.source = create_manufacturer(actor=self.owner, name="Old supplier")
        self.target = create_manufacturer(actor=self.owner, name="New supplier")
        self.product = create_product(
            actor=self.owner, sku="RESOLVE", name="Shared supplement",
            manufacturer=self.source, base_retail_price=Decimal("100"), currency="PKR",
        )
        self.package = ProductPackagePrice.objects.create(
            product=self.product, manufacturer=self.source, amount=60, price=25,
            created_by=self.owner, updated_by=self.owner,
        )

    def resolve(self, action, **kwargs):
        return delete_manufacturer(
            actor=self.owner, manufacturer_id=self.source.pk,
            review_token=review_manufacturer(self.source)["token"],
            product_actions={str(self.product.pk): {"action": action, **kwargs}},
        )

    def test_review_lists_direct_and_package_only_products_once(self):
        review = review_manufacturer(self.source)
        self.assertEqual([row["product"].pk for row in review["products"]], [self.product.pk])
        self.assertEqual(review["products"][0]["packages"][0].pk, self.package.pk)
        self.product.manufacturer = None
        self.product.save()
        self.assertEqual(len(review_manufacturer(self.source)["products"]), 1)

    def test_reassign_moves_product_and_package_prices(self):
        result = self.resolve("reassign", target_id=str(self.target.pk))
        self.product.refresh_from_db()
        self.package.refresh_from_db()
        self.assertEqual(self.product.manufacturer_id, self.target.pk)
        self.assertEqual(self.package.manufacturer_id, self.target.pk)
        self.assertTrue(self.product.active)
        self.assertFalse(result["archived"])
        self.assertFalse(Manufacturer.objects.filter(pk=self.source.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(action="product.manufacturer_reassigned").exists())

    def test_existing_target_price_wins_for_same_package_amount(self):
        existing = ProductPackagePrice.objects.create(
            product=self.product, manufacturer=self.target, amount=60, price=99,
            created_by=self.owner, updated_by=self.owner,
        )
        self.resolve("reassign", target_id=str(self.target.pk))
        existing.refresh_from_db()
        self.assertEqual(existing.price, Decimal("99"))
        self.assertEqual(self.product.package_prices.count(), 1)
        self.assertFalse(ProductPackagePrice.objects.filter(pk=self.package.pk).exists())

    def test_hold_keeps_product_accessible_to_owner_and_removes_old_supplier_prices(self):
        self.resolve("hold")
        self.product.refresh_from_db()
        self.assertFalse(self.product.active)
        self.assertIsNone(self.product.manufacturer_id)
        self.assertIsNone(self.product.archived_at)
        self.assertTrue(Product.objects.for_user(self.owner).filter(pk=self.product.pk).exists())
        self.assertFalse(ProductPackagePrice.objects.filter(pk=self.package.pk).exists())

    def test_delete_removes_unused_product(self):
        self.resolve("delete")
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(action="product.deleted").exists())

    def test_history_is_preserved_by_archiving_product_and_manufacturer(self):
        from .services import create_manufacturer_order

        order = create_manufacturer_order(
            actor=self.owner, manufacturer=self.source,
            items=[{"product":self.product, "quantity":Decimal("2"), "unit_price":Decimal("10")}],
        )
        self.resolve("delete")
        self.product.refresh_from_db()
        self.source.refresh_from_db()
        order.refresh_from_db()
        self.assertIsNotNone(self.source.archived_at)
        self.assertIsNotNone(self.product.archived_at)
        self.assertFalse(self.source.active)
        self.assertFalse(self.product.active)
        self.assertEqual(order.manufacturer_id, self.source.pk)
        self.assertEqual(order.items.get().product_id, self.product.pk)
        self.assertEqual(order.manufacturer.name, "Old supplier")
        self.assertFalse(Product.objects.for_user(self.owner).filter(pk=self.product.pk).exists())

    def test_manufacturer_order_is_not_reassigned_with_product(self):
        order = ManufacturerOrder.objects.create(
            manufacturer=self.source, po_number="MO-RESOLVE", created_by=self.owner,
        )
        self.resolve("reassign", target_id=str(self.target.pk))
        self.source.refresh_from_db()
        order.refresh_from_db()
        self.assertIsNotNone(self.source.archived_at)
        self.assertEqual(order.manufacturer_id, self.source.pk)

    def test_supplier_warehouse_is_preserved(self):
        from apps.owner_warehouse.models import Location

        location = Location.objects.create(
            code="RESOLVE-WH", name="Supplier warehouse", location_type="SUPPLIER",
            manufacturer=self.source, created_by=self.owner,
        )
        self.resolve("hold")
        self.source.refresh_from_db()
        location.refresh_from_db()
        self.assertIsNotNone(self.source.archived_at)
        self.assertEqual(location.manufacturer_id, self.source.pk)

    def test_missing_decisions_and_unknown_products_are_rejected(self):
        for actions in ({}, {"unknown": {"action":"delete"}}):
            with self.subTest(actions=actions), self.assertRaises(ValidationError):
                delete_manufacturer(
                    actor=self.owner, manufacturer_id=self.source.pk,
                    review_token=review_manufacturer(self.source)["token"], product_actions=actions,
                )
        self.assertTrue(Manufacturer.objects.filter(pk=self.source.pk).exists())

    def test_invalid_action_or_target_leaves_everything_unchanged(self):
        for action, target in (("bad", None), ("reassign", None), ("reassign", str(self.source.pk)), ("reassign", "invalid")):
            with self.subTest(action=action, target=target), self.assertRaises(ValidationError):
                self.resolve(action, target_id=target)
        self.assertEqual(self.product.package_prices.count(), 1)
        self.assertFalse(AuditEvent.objects.filter(action="manufacturer.deleted").exists())

    def test_inactive_target_is_rejected(self):
        self.target.active = False
        self.target.save()
        with self.assertRaises(ValidationError):
            self.resolve("reassign", target_id=str(self.target.pk))

    def test_changed_links_or_price_invalidate_review(self):
        token = review_manufacturer(self.source)["token"]
        self.package.price = 88
        self.package.save()
        with self.assertRaisesMessage(ValidationError, "changed"):
            delete_manufacturer(
                actor=self.owner, manufacturer_id=self.source.pk, review_token=token,
                product_actions={str(self.product.pk):{"action":"hold"}},
            )
        self.product.refresh_from_db()
        self.assertTrue(self.product.active)

    def test_tampered_token_and_nonowner_are_rejected(self):
        with self.assertRaises(ValidationError):
            delete_manufacturer(actor=self.owner, manufacturer_id=self.source.pk, review_token="tampered")
        distributor = User.objects.create_user(email="dist@resolve.test", role="DISTRIBUTOR")
        with self.assertRaises(PermissionDenied):
            delete_manufacturer(actor=distributor, manufacturer_id=self.source.pk)

    def test_product_shared_with_other_manufacturer_keeps_other_prices_on_hold(self):
        other = ProductPackagePrice.objects.create(
            product=self.product, manufacturer=self.target, amount=120, price=50,
            created_by=self.owner,
        )
        self.resolve("hold")
        self.assertTrue(ProductPackagePrice.objects.filter(pk=other.pk).exists())

    def test_archived_manufacturer_cannot_be_deleted_twice(self):
        ManufacturerOrder.objects.create(manufacturer=self.source, po_number="MO-ARCHIVE")
        self.resolve("hold")
        with self.assertRaises(ValidationError):
            delete_manufacturer(actor=self.owner, manufacturer_id=self.source.pk)

    def test_audit_failure_rolls_back_product_and_manufacturer_changes(self):
        from unittest.mock import patch

        with patch("apps.manufacturers.deletion.record_audit_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.resolve("hold")
        self.product.refresh_from_db()
        self.assertTrue(self.product.active)
        self.assertEqual(self.product.manufacturer_id, self.source.pk)
        self.assertTrue(ProductPackagePrice.objects.filter(pk=self.package.pk).exists())

    def test_removed_supplier_and_archived_product_cannot_be_used_on_new_orders(self):
        from .services import create_manufacturer_order, update_manufacturer

        ManufacturerOrder.objects.create(manufacturer=self.source, po_number="MO-OLD")
        self.resolve("hold")
        with self.assertRaises(ValidationError):
            update_manufacturer(actor=self.owner, manufacturer_id=self.source.pk, active=True)
        with self.assertRaises(ValidationError):
            create_manufacturer_order(
                actor=self.owner, manufacturer=self.source,
                items=[{"product":self.product, "quantity":1, "unit_price":10}],
            )

    def test_distributor_cannot_order_a_held_product_using_stale_reference(self):
        from apps.distributors.services import create_distributor, approve_distributor
        from apps.requests.services import create_purchase_order, create_request_to_quote

        distributor = create_distributor(
            actor=self.owner, email="buyer@resolve.test", temporary_password="Temporary123!",
            name="Buyer",
        )
        approve_distributor(user=self.owner, distributor_id=distributor.pk)
        distributor.refresh_from_db()
        self.resolve("hold")
        for create in (create_purchase_order, create_request_to_quote):
            with self.subTest(create=create), self.assertRaises(ValidationError):
                create(actor=distributor, items=[{"product":self.product, "quantity_requested":1}])
