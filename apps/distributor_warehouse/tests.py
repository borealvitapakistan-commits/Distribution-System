from django.core.exceptions import ValidationError
from django.test import Client, TestCase

from apps.accounts.models import User
from apps.distributors.models import DistributorProfile
from apps.distributors.services import approve_distributor, create_distributor

from .forms import DistributorLocationForm
from .models import DistributorInventory, DistributorLocation
from .services import create_inventory, create_location


class DistributorWarehouseRulesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@distwarehouse.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.distributor_user = create_distributor(
            actor=self.owner,
            email="dist@distwarehouse.test",
            temporary_password="TemporaryPassword123!",
            name="Warehouse Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=self.distributor_user.pk)
        self.distributor_user.refresh_from_db()
        self.distributor_profile = DistributorProfile.objects.get(
            user=self.distributor_user
        )

        self.other_distributor_user = create_distributor(
            actor=self.owner,
            email="other@distwarehouse.test",
            temporary_password="TemporaryPassword123!",
            name="Other Distributor",
        )
        approve_distributor(
            user=self.owner, distributor_id=self.other_distributor_user.pk
        )
        self.other_distributor_user.refresh_from_db()

    def test_region_code_is_unique_per_distributor_not_globally(self):
        create_inventory(actor=self.distributor_user, code="LHR", name="Lahore")

        with self.assertRaises(ValidationError):
            create_inventory(actor=self.distributor_user, code="LHR", name="Again")

        # A different Distributor can reuse the same code — namespaces
        # are per-distributor, not shared.
        other_region = create_inventory(
            actor=self.other_distributor_user, code="LHR", name="Also Lahore"
        )
        self.assertEqual(other_region.code, "LHR")

    def test_warehouse_requires_a_region(self):
        with self.assertRaises(ValidationError):
            create_location(
                actor=self.distributor_user,
                code="NO-REGION",
                name="No Region Warehouse",
                location_type=DistributorLocation.LocationType.WAREHOUSE,
            )

    def test_warehouse_region_must_belong_to_same_distributor(self):
        other_region = create_inventory(
            actor=self.other_distributor_user, code="KHI", name="Karachi"
        )

        with self.assertRaises(ValidationError):
            create_location(
                actor=self.distributor_user,
                code="CROSS-WH",
                name="Cross Distributor Warehouse",
                location_type=DistributorLocation.LocationType.WAREHOUSE,
                distributor_inventory=other_region,
            )

    def test_distributor_only_sees_their_own_regions_and_warehouses(self):
        region = create_inventory(
            actor=self.distributor_user, code="ISB", name="Islamabad"
        )
        create_location(
            actor=self.distributor_user,
            code="ISB-WH1",
            name="Islamabad Warehouse",
            location_type=DistributorLocation.LocationType.WAREHOUSE,
            distributor_inventory=region,
        )
        create_inventory(
            actor=self.other_distributor_user, code="ISB2", name="Also Islamabad"
        )

        self.assertEqual(
            DistributorInventory.objects.for_user(self.distributor_user).count(), 1
        )
        self.assertEqual(
            DistributorLocation.objects.for_user(self.distributor_user)
            .filter(location_type=DistributorLocation.LocationType.WAREHOUSE)
            .count(),
            1,
        )

    def test_form_has_no_manual_type_choice(self):
        form = DistributorLocationForm(distributor_profile=self.distributor_profile)
        self.assertNotIn("location_type", form.fields)

    def test_warehouse_pages_are_connected(self):
        region = create_inventory(
            actor=self.distributor_user, code="PAGES", name="Pages Region"
        )
        warehouse = create_location(
            actor=self.distributor_user,
            code="PAGES-WH",
            name="Pages Warehouse",
            location_type=DistributorLocation.LocationType.WAREHOUSE,
            distributor_inventory=region,
        )

        client = Client()
        client.force_login(self.distributor_user)

        for route in [
            "/distributor/warehouse/regions/",
            "/distributor/warehouse/regions/new/",
            f"/distributor/warehouse/regions/{region.pk}/",
            f"/distributor/warehouse/regions/{region.pk}/edit/",
            "/distributor/warehouse/locations/",
            "/distributor/warehouse/locations/new/",
            f"/distributor/warehouse/locations/{warehouse.pk}/",
            f"/distributor/warehouse/locations/{warehouse.pk}/edit/",
        ]:
            with self.subTest(route=route):
                response = client.get(route)
                self.assertEqual(response.status_code, 200)

    def test_owner_cannot_access_distributor_warehouse_pages(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get("/distributor/warehouse/regions/")
        self.assertEqual(response.status_code, 403)

    def test_other_distributor_cannot_view_someones_elses_warehouse(self):
        region = create_inventory(
            actor=self.distributor_user, code="PRIV", name="Private Region"
        )
        warehouse = create_location(
            actor=self.distributor_user,
            code="PRIV-WH",
            name="Private Warehouse",
            location_type=DistributorLocation.LocationType.WAREHOUSE,
            distributor_inventory=region,
        )

        client = Client()
        client.force_login(self.other_distributor_user)

        response = client.get(f"/distributor/warehouse/locations/{warehouse.pk}/")
        self.assertEqual(response.status_code, 404)
