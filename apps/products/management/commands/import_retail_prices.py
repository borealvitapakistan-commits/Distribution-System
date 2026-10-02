from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.products.models import Product
from apps.products.services import import_retail_price_sheet, update_product


DEFAULT_SHEET = Path(settings.BASE_DIR) / "data" / "retail_price_sheet.csv"


class Command(BaseCommand):
    help = (
        "Loads retail prices per bottle size (30/60/90/120 capsules) from a "
        "price sheet CSV. Products given at least one price are switched to "
        "the Bottle unit of measure."
    )

    def add_arguments(self, parser):
        parser.add_argument("sheet", nargs="?", default=str(DEFAULT_SHEET))

    def handle(self, *args, sheet, **options):
        owner = User.objects.filter(role=User.Role.OWNER, is_active=True).first()
        if owner is None:
            raise CommandError("No active Owner user found.")

        path = Path(sheet)
        if not path.exists():
            raise CommandError(f"Price sheet not found: {path}")

        try:
            with path.open(newline="", encoding="utf-8-sig") as file:
                updated = import_retail_price_sheet(actor=owner, file=file)

            priced = Product.objects.filter(retail_prices__isnull=False).exclude(
                unit_of_measure=Product.UnitOfMeasure.BOTTLE
            ).distinct()
            for product in priced:
                update_product(
                    actor=owner,
                    product_id=product.pk,
                    unit_of_measure=Product.UnitOfMeasure.BOTTLE,
                )
                self.stdout.write(f"Unit of measure set to Bottle: {product.sku}")
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))

        self.stdout.write(self.style.SUCCESS(f"Retail prices loaded for {updated} product(s)."))
