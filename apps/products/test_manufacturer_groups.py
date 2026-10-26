from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

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

    def test_catalog_exposes_every_manufacturer_and_complete_group_rows(self):
        create_manufacturer(actor=self.owner,name="Empty group")
        self.client.force_login(self.owner)
        response = self.client.get(reverse("product-list"))
        self.assertEqual(len(response.context["manufacturer_groups"]),3)
        self.assertContains(response,"Empty group")
        response = self.client.get(reverse("product-manufacturer-products",args=[self.other.pk]))
        self.assertContains(response,"Ashwagandha (OM)")
        self.assertEqual(response.context["page_obj"].paginator.count,1)
        self.assertContains(response,f"?manufacturer={self.other.pk}")

    def test_group_pagination_includes_products_beyond_fifty(self):
        for number in range(51):
            create_product(actor=self.owner,name=f"More {number:02}",sku=f"GROUP-{number}",manufacturer=self.glorious)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("product-manufacturer-products",args=[self.glorious.pk]),{"page":2})
        self.assertEqual(response.context["page_obj"].paginator.count,52)
        self.assertEqual(len(response.context["products"]),2)

    def test_product_heading_uses_context_prefix_without_renaming_record(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("product-detail",args=[self.product.pk]),{"manufacturer":self.other.pk})
        self.assertContains(response,"Ashwagandha (OM)")
        self.assertEqual(response.context["form"]["name"].value(),"Ashwagandha")
        self.assertContains(response,"Editing its details updates it in every group.")
        self.product.refresh_from_db()
        self.assertEqual(self.product.name,"Ashwagandha")

    def test_invalid_manufacturer_context_is_rejected_before_editing(self):
        unrelated = create_manufacturer(actor=self.owner,name="Unrelated")
        self.client.force_login(self.owner)
        url = reverse("product-detail",args=[self.product.pk])
        for key in (unrelated.pk,"invalid"):
            self.assertEqual(self.client.get(url,{"manufacturer":key}).status_code,404)
            self.assertEqual(self.client.post(f"{url}?manufacturer={key}",{"name":"Wrong change"}).status_code,404)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name,"Ashwagandha")

    def test_add_product_preselects_manufacturer_and_saves_association(self):
        from .forms import ProductForm

        self.client.force_login(self.owner)
        url = reverse("product-create")+f"?manufacturer={self.glorious.pk}"
        response = self.client.get(url)
        self.assertEqual(response.context["form"]["manufacturers"].value(),[self.glorious.pk])
        form=ProductForm()
        data={name:form[name].value() if form[name].value() is not None else "" for name in form.fields
              if name not in ("image","label_file","bottle_image","allow_fractional_quantity","gallery_photos","manufacturers")}
        data.update({"name":"New herb","sku":"NEW-GP","manufacturers":[self.glorious.pk,self.other.pk],
                     "ingredients-TOTAL_FORMS":"0","ingredients-INITIAL_FORMS":"0"})
        response=self.client.post(url,data)
        product=Product.objects.get(sku="NEW-GP")
        self.assertEqual(product.manufacturer_id,self.glorious.pk)
        self.assertEqual(set(product.allocations.values_list("manufacturer_id",flat=True)),{self.glorious.pk,self.other.pk})
        self.assertRedirects(response,reverse("product-detail",args=[product.pk])+f"?manufacturer={self.glorious.pk}")

    def test_search_and_status_filters_apply_within_manufacturer(self):
        self.client.force_login(self.owner)
        url=reverse("product-manufacturer-products",args=[self.other.pk])
        self.assertContains(self.client.get(url,{"q":"ashwa"}),"Ashwagandha (OM)")
        self.assertNotContains(self.client.get(url,{"q":"does-not-match"}),"Ashwagandha (OM)")
        self.assertEqual(self.client.get(url,{"status":"inactive"}).context["page_obj"].paginator.count,0)

    def test_duplicate_bulk_selections_update_shared_product_once(self):
        self.client.force_login(self.owner)
        response=self.client.post(reverse("product-actions"),{"products":[self.product.pk,self.product.pk],"action":"hold"})
        self.assertRedirects(response,reverse("product-list"))
        self.product.refresh_from_db()
        self.assertFalse(self.product.active)

    def test_group_endpoint_rejects_distributor_and_archived_manufacturer(self):
        from django.utils import timezone

        self.glorious.archived_at=timezone.now()
        self.glorious.active=False
        self.glorious.save()
        self.client.force_login(self.owner)
        url=reverse("product-manufacturer-products",args=[self.glorious.pk])
        self.assertEqual(self.client.get(url).status_code,404)
        user=User.objects.create_user(email="buyer@groups.test",role="DISTRIBUTOR")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("product-manufacturer-products",args=[self.other.pk])).status_code,403)


class ManufacturerAllocationTests(TestCase):
    def setUp(self):
        from .services import set_product_manufacturers

        self.set_product_manufacturers = set_product_manufacturers
        self.owner = User.objects.create_user(email="alloc@test.local", role="OWNER")
        self.first = create_manufacturer(actor=self.owner, name="Alpha Labs", prefix="AL")
        self.second = create_manufacturer(actor=self.owner, name="Beta Pharma", prefix="BP")
        self.product = create_product(actor=self.owner, name="Moringa", sku="ALLOC-MOR")
        self.client.force_login(self.owner)

    def prices(self, manufacturer):
        return dict(self.product.package_prices.filter(manufacturer=manufacturer).values_list("amount", "price"))

    def test_allocated_product_without_prices_appears_under_each_manufacturer(self):
        self.set_product_manufacturers(actor=self.owner, product=self.product, manufacturers=[self.first, self.second])
        groups = manufacturer_groups(Product.objects.all(), [self.first, self.second])
        self.assertEqual([row["count"] for row in groups], [1, 1])
        self.product.refresh_from_db()
        self.assertEqual(self.product.manufacturer, self.first)

    def test_each_manufacturer_keeps_its_own_sizes_and_prices(self):
        self.set_product_manufacturers(actor=self.owner, product=self.product, manufacturers=[self.first, self.second])
        url = lambda m: reverse("product-manufacturer-packages", args=[self.product.pk, m.pk])
        self.client.post(url(self.first), {"amount": ["30", "40", "90"], "price": ["900", "1,150", "2400"]})
        self.client.post(url(self.second), {"amount": ["60", ""], "price": ["1500", ""]})
        self.assertEqual(self.prices(self.first), {Decimal(30): Decimal(900), Decimal(40): Decimal(1150), Decimal(90): Decimal(2400)})
        self.assertEqual(self.prices(self.second), {Decimal(60): Decimal(1500)})

        # Changing one price, adding a size and dropping one only touches that manufacturer.
        self.client.post(url(self.first), {"amount": ["30", "50"], "price": ["950", "1300"]})
        self.assertEqual(self.prices(self.first), {Decimal(30): Decimal(950), Decimal(50): Decimal(1300)})
        self.assertEqual(self.prices(self.second), {Decimal(60): Decimal(1500)})

    def test_invalid_rows_change_nothing(self):
        url = reverse("product-manufacturer-packages", args=[self.product.pk, self.first.pk])
        self.client.post(url, {"amount": ["30"], "price": ["900"]})
        for data in ({"amount": ["30", "30"], "price": ["1", "2"]}, {"amount": ["60"], "price": [""]},
                     {"amount": ["abc"], "price": ["5"]}, {"amount": ["0"], "price": ["5"]}):
            response = self.client.post(url, data)
            self.assertRedirects(response, reverse("product-detail", args=[self.product.pk]) + f"#maker-{self.first.pk}")
            self.assertEqual(self.prices(self.first), {Decimal(30): Decimal(900)})

    def test_unallocating_removes_that_manufacturers_prices_only(self):
        self.set_product_manufacturers(actor=self.owner, product=self.product, manufacturers=[self.first, self.second])
        ProductPackagePrice.objects.create(product=self.product, manufacturer=self.first, amount=30, price=1)
        ProductPackagePrice.objects.create(product=self.product, manufacturer=self.second, amount=60, price=2)
        self.set_product_manufacturers(actor=self.owner, product=self.product, manufacturers=[self.second])
        self.product.refresh_from_db()
        self.assertEqual(self.product.manufacturer, self.second)
        self.assertEqual(self.prices(self.first), {})
        self.assertEqual(self.prices(self.second), {Decimal(60): Decimal(2)})
        self.assertEqual(list(products_for_manufacturer(Product.objects.all(), self.first)), [])

    def test_product_page_shows_a_price_card_per_allocated_manufacturer(self):
        self.set_product_manufacturers(actor=self.owner, product=self.product, manufacturers=[self.first, self.second])
        response = self.client.get(reverse("product-detail", args=[self.product.pk]), {"manufacturer": self.second.pk})
        cards = response.context["manufacturer_pricing"]
        self.assertEqual([card["manufacturer"] for card in cards], [self.second, self.first])
        self.assertTrue(cards[0]["open"])
        self.assertContains(response, reverse("product-manufacturer-packages", args=[self.product.pk, self.first.pk]))
