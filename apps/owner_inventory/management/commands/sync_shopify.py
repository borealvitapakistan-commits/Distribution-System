from django.core.management.base import BaseCommand, CommandError

from apps.owner_inventory import shopify_sync
from apps.products.shopify import ShopifyError


class Command(BaseCommand):
    help = (
        "Import new Shopify orders as sales, then set Shopify's stock to "
        "this system's sellable totals. Run it on a schedule (e.g. every "
        "5 minutes) so online sales reach the inventory promptly."
    )

    def handle(self, *args, **options):
        try:
            summary = shopify_sync.sync()
        except ShopifyError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            f"{summary['orders_imported']} new order(s), "
            f"{summary['lines_posted']} line(s) taken from stock, "
            f"{summary['lines_short']} short, {summary['lines_unlinked']} unlinked, "
            f"{summary['products_pushed']} product(s) pushed."
        )
        for warning in summary["warnings"]:
            self.stderr.write(warning)
