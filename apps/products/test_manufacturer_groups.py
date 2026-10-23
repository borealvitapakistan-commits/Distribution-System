from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import User
from apps.manufacturers.models import Manufacturer
from apps.manufacturers.services import create_manufacturer, update_manufacturer
from apps.owner_inventory.models import StockBalance
from apps.owner_warehouse.models import Location

from .models import Product, ProductPackagePrice
from .services import create_product
from .workspace import manufacturer_groups, products_for_manufacturer, unassigned_products, with_inventory


class ManufacturerCatalogTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="groups@test.local", role="OWNER")
        self.glorious = create_manufacturer(actor=self.owner, name="Glorious Properties", prefix="GP")
        self.other = create_manufacturer(actor=self.owner, name="Other manufacturer", prefix="OM")
        self.product = create_product(actor=self.owner, name="Ashwagandha", sku="GROUP-ASH", manufacturer=self.glorious)
        for manufacturer, amount in ((self.glorious,60), (self.other,60), (self.other,120)):
            ProductPackagePrice.objects.create(product=self.product, manufacturer=manufacturer, amount=amount, price=100)

    def test_prefix_is_editable_trimmed_and_unique_ignoring_case(self):
        update_manufacturer(actor=self.owner, manufacturer_id=self.glorious.pk, prefix=" GL ")
        self.glorious.refresh_from_db()
        self.assertEqual(self.glorious.product_label, "GL")
        with self.assertRaises(ValidationError):
            update_manufacturer(actor=self.owner, manufacturer_id=self.other.pk, prefix="gl")
        self.other.refresh_from_db()
        self.assertEqual(self.other.prefix, "OM")

    def test_blank_prefix_uses_manufacturer_name(self):
        manufacturer = create_manufacturer(actor=self.owner, name="No prefix")
        self.assertEqual(manufacturer.product_label, "No prefix")

    def test_shared_product_is_counted_once_for_each_manufacturer(self):
        groups = manufacturer_groups(Product.objects.all(), [self.glorious,self.other])
        self.assertEqual([row["count"] for row in groups], [1,1])
        self.assertEqual(list(products_for_manufacturer(Product.objects.all(),self.other)), [self.product])

    def test_inventory_is_not_multiplied_by_supplier_package_links(self):
        location = Location.objects.create(code="GROUP-WH", name="Warehouse", location_type="OWN", is_sellable=True)
        StockBalance.objects.create(product=self.product,location=location,quantity=7)
        queryset = with_inventory(Product.objects.all())
        self.assertEqual(products_for_manufacturer(queryset,self.other).get().inventory_available, Decimal(7))

    def test_empty_manufacturers_and_unassigned_products_stay_accessible(self):
        empty = create_manufacturer(actor=self.owner, name="Empty supplier")
        loose = create_product(actor=self.owner,name="Unassigned product",sku="GROUP-LOOSE")
        groups = manufacturer_groups(Product.objects.all(), [self.glorious,self.other,empty])
        self.assertEqual(groups[2]["count"], 0)
        self.assertEqual(groups[3]["key"], "unassigned")
        self.assertEqual(list(unassigned_products(Product.objects.all())), [loose])
