from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import User
from apps.customers.services import create_customer
from apps.distributors.models import DistributorProfile
from apps.distributors.services import create_distributor
from apps.manufacturers.services import create_manufacturer

from .models import Location
from .services import create_location


class WarehouseRulesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@warehouse.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        distributor_user = create_distributor(
            actor=self.owner,
            email="dist@warehouse.test",
            temporary_password="TemporaryPassword123!",
            name="Warehouse Distributor",
        )
        self.distributor_profile = DistributorProfile.objects.get(user=distributor_user)
        self.manufacturer = create_manufacturer(
            actor=self.owner,
            name="Warehouse Manufacturer",
        )
        self.customer = create_customer(
            actor=self.owner,
            name="Warehouse Customer",
        )

    def test_location_truth_table(self):
        cases = [
            ("OWN", {}, (True, True, True)),
            ("SUPPLIER", {"manufacturer": self.manufacturer}, (False, False, False)),
            ("CUSTOMER", {}, (False, False, False)),
            (
                "DISTRIBUTOR",
                {"distributor_profile": self.distributor_profile},
                (True, True, True),
            ),
        ]

        for index, (location_type, links, expected) in enumerate(cases):
            location = create_location(
                actor=self.owner,
                code=f"LOC-{index}",
                name=f"Location {index}",
                location_type=location_type,
                is_sellable=(location_type == "DISTRIBUTOR"),
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
                code="BAD-SUPPLIER",
                name="Wrong Supplier Location",
                location_type=Location.LocationType.SUPPLIER,
                distributor_profile=self.distributor_profile,
            )

        with self.assertRaises(ValidationError):
            create_location(
                actor=self.owner,
                code="BAD-DISTRIBUTOR",
                name="Wrong Distributor Location",
                location_type=Location.LocationType.DISTRIBUTOR,
                manufacturer=self.manufacturer,
            )

        with self.assertRaises(ValidationError):
            create_location(
                actor=self.owner,
                code="MISSING-SUPPLIER",
                name="Missing Manufacturer Link",
                location_type=Location.LocationType.SUPPLIER,
            )
