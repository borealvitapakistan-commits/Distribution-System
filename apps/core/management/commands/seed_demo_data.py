from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.manufacturers.models import Manufacturer
from apps.manufacturers.services import create_manufacturer
from apps.products.models import Product
from apps.products.services import create_product


MANUFACTURERS = [
    {
        "name": "NutraCore Labs",
        "phone": "+92-42-1110001",
        "email": "sales@nutracorelabs.example",
        "address": "Plot 14, Industrial Estate, Lahore",
    },
    {
        "name": "GreenLeaf Pharmaceuticals",
        "phone": "+92-42-1110002",
        "email": "contact@greenleafpharma.example",
        "address": "Sundar Industrial Zone, Lahore",
    },
    {
        "name": "Vitality Biosciences",
        "phone": "+92-21-1110003",
        "email": "info@vitalitybio.example",
        "address": "SITE Area, Karachi",
    },
    {
        "name": "PureHerb Manufacturing Co.",
        "phone": "+92-51-1110004",
        "email": "orders@pureherbmfg.example",
        "address": "Industrial Triangle, Islamabad",
    },
    {
        "name": "Wellness Pharma Industries",
        "phone": "+92-41-1110005",
        "email": "hello@wellnesspharma.example",
        "address": "Khurrianwala Industrial Estate, Faisalabad",
    },
]

PRODUCTS = [
    {
        "sku": "BV-VITC-1000",
        "name": "Vitamin C 1000mg Tablets",
        "base_retail_price": Decimal("850.00"),
    },
    {
        "sku": "BV-MULTI-30",
        "name": "Daily Multivitamin Capsules (30ct)",
        "base_retail_price": Decimal("1200.00"),
    },
    {
        "sku": "BV-OMEGA3-60",
        "name": "Omega-3 Fish Oil Softgels (60ct)",
        "base_retail_price": Decimal("1850.00"),
    },
    {
        "sku": "BV-ZINC-50",
        "name": "Zinc Picolinate 50mg Tablets",
        "base_retail_price": Decimal("650.00"),
    },
    {
        "sku": "BV-CALMAG-90",
        "name": "Calcium Magnesium Complex (90ct)",
        "base_retail_price": Decimal("1450.00"),
    },
    {
        "sku": "BV-PROB-20B",
        "name": "Probiotic 20 Billion CFU Capsules",
        "base_retail_price": Decimal("2100.00"),
    },
    {
        "sku": "BV-ASHW-500",
        "name": "Ashwagandha Root Extract 500mg",
        "base_retail_price": Decimal("990.00"),
    },
    {
        "sku": "BV-COLLA-300",
        "name": "Marine Collagen Powder 300g",
        "base_retail_price": Decimal("3200.00"),
    },
    {
        "sku": "BV-IMMUNE-SYR",
        "name": "Immunity Booster Syrup 200ml",
        "base_retail_price": Decimal("750.00"),
    },
    {
        "sku": "BV-BIOTIN-5000",
        "name": "Biotin 5000mcg Capsules",
        "base_retail_price": Decimal("880.00"),
    },
]


class Command(BaseCommand):
    help = "Seeds 5 manufacturers and 10 products for local/demo use."

    def handle(self, *args, **options):
        owner = User.objects.filter(
            role=User.Role.OWNER, is_active=True
        ).first()

        if owner is None:
            raise CommandError(
                "No active Owner user found. Create one before seeding."
            )

        created_manufacturers = 0
        for data in MANUFACTURERS:
            if Manufacturer.objects.filter(name=data["name"]).exists():
                self.stdout.write(f"Skipping existing manufacturer: {data['name']}")
                continue

            try:
                create_manufacturer(actor=owner, **data)
            except ValidationError as exc:
                raise CommandError(f"Could not create {data['name']}: {exc}")

            created_manufacturers += 1
            self.stdout.write(f"Created manufacturer: {data['name']}")

        created_products = 0
        for data in PRODUCTS:
            if Product.objects.filter(sku=data["sku"]).exists():
                self.stdout.write(f"Skipping existing product: {data['sku']}")
                continue

            try:
                create_product(
                    actor=owner,
                    barcode="",
                    currency="PKR",
                    **data,
                )
            except ValidationError as exc:
                raise CommandError(f"Could not create {data['sku']}: {exc}")

            created_products += 1
            self.stdout.write(f"Created product: {data['sku']} - {data['name']}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {created_manufacturers} manufacturer(s) and "
                f"{created_products} product(s) created."
            )
        )
