from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import ListView

from apps.accounts.mixins import OwnerRequiredMixin
from apps.batches.models import Batch
from apps.batches.services import trace_batch_code
from apps.products.shopify import ShopifyError

from .forms import GiveToDistributorForm, ReceiveStockForm
from . import shopify_sync
from .models import ShopifyOrder, ShopifySyncState, StockBatch, StockMovement
from .services import give_to_distributor, receive_stock


class StockBalanceListView(OwnerRequiredMixin, ListView):
    """Stock on hand, one row per batch. Two orders of the same product
    are two separate lots — each with its own quantity and expiry — so
    they're never added together into one line."""

    template_name = "owner_inventory/stock_balance_list.html"
    context_object_name = "batches"

    def get_queryset(self):
        return (
            StockBatch.objects
            .for_user(self.request.user)
            .select_related("product", "location")
            .available()
            .fefo_ordered()
        )


class StockBatchListView(OwnerRequiredMixin, ListView):
    template_name = "owner_inventory/stock_batch_list.html"
    context_object_name = "batches"

    def get_queryset(self):
        return (
            StockBatch.objects
            .for_user(self.request.user)
            .select_related("product", "location", "location__inventory")
            .available()
            .fefo_ordered()
        )


class StockMovementListView(OwnerRequiredMixin, ListView):
    template_name = "owner_inventory/stock_movement_list.html"
    context_object_name = "movements"
    paginate_by = 50

    def get_queryset(self):
        movements = (
            StockMovement.objects
            .for_user(self.request.user)
            .select_related(
                "product", "from_location", "to_location", "created_by"
            )
        )

        channel = self.request.GET.get("channel", "")
        if channel in StockMovement.Channel.values:
            movements = movements.filter(channel=channel)

        product_id = self.request.GET.get("product")
        if product_id:
            try:
                movements = movements.filter(product_id=product_id)
            except ValidationError:
                return movements.none()

        return movements

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["channels"] = StockMovement.Channel.choices
        context["channel"] = self.request.GET.get("channel", "")
        return context


class ReceiveStockView(OwnerRequiredMixin, View):
    template_name = "owner_inventory/receive_stock_form.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {"form": ReceiveStockForm(initial={
                "product": request.GET.get("product"), "to_location": request.GET.get("location"),
            })},
        )

    def post(self, request):
        form = ReceiveStockForm(request.POST)

        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        try:
            receive_stock(
                actor=request.user,
                product=form.cleaned_data["product"],
                quantity=form.cleaned_data["quantity"],
                to_location=form.cleaned_data["to_location"],
                reference=form.cleaned_data["reference"],
                received_date=form.cleaned_data["received_date"],
                expiry_date=form.cleaned_data["expiry_date"],
                batch_number=form.cleaned_data["batch_number"],
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form})

        messages.success(request, "Inventory received successfully.")
        return redirect("stock-balance-list")


class GiveToDistributorView(OwnerRequiredMixin, View):
    template_name = "owner_inventory/give_to_distributor_form.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {"form": GiveToDistributorForm()},
        )

    def post(self, request):
        form = GiveToDistributorForm(request.POST)

        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        try:
            batch = form.cleaned_data["batch"]
            give_to_distributor(
                actor=request.user,
                product=batch.product,
                quantity=form.cleaned_data["quantity"],
                from_location=batch.location,
                distributor_profile=form.cleaned_data["distributor"],
                batch=batch,
                reference=form.cleaned_data["reference"],
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form})

        messages.success(request, "Stock sent to Distributor successfully.")
        return redirect("stock-balance-list")


class BatchTraceView(OwnerRequiredMixin, View):
    """Search one batch code and see its whole journey — Manufacturer,
    Owner, every Distributor it reached and every sub-distributor it was
    sold on to — with the quantities checked against each other."""

    template_name = "owner_inventory/batch_trace.html"

    def get(self, request):
        code = request.GET.get("code", "").strip()
        known_codes = sorted(
            set(Batch.objects.values_list("code", flat=True))
            | set(
                StockBatch.objects
                .exclude(batch_number="")
                .values_list("batch_number", flat=True)
            )
        )

        return render(
            request,
            self.template_name,
            {
                "code": code,
                "known_codes": known_codes,
                "trace": trace_batch_code(code) if code else None,
            },
        )


class ShopifySyncView(OwnerRequiredMixin, View):
    """Shopify sells from the same stock as everything else: this page
    shows each linked product's sellable total next to what Shopify
    shows, the Shopify orders recorded as sales, and a Sync now button."""

    template_name = "owner_inventory/shopify_sync.html"

    def get(self, request):
        rows, live_error = shopify_sync.status_rows()

        # Shopify's own product list, for "link to an existing Shopify
        # product" — only when this product is already sold there under
        # another name or SKU.
        shopify_products = []
        if shopify_sync.shopify.is_configured() and not live_error:
            try:
                shopify_products = shopify_sync.shopify.list_products()
            except ShopifyError as exc:
                live_error = str(exc)

        to_publish = [
            {
                "product": product,
                "quantity": quantity,
                "match": shopify_sync.match_on_shopify(product, shopify_products)[0],
            }
            for product, quantity in shopify_sync.products_to_publish()
        ]

        return render(
            request,
            self.template_name,
            {
                "configured": shopify_sync.shopify.is_configured(),
                "enabled": settings.SHOPIFY_INVENTORY_SYNC,
                "state": ShopifySyncState.load(),
                "rows": rows,
                "to_publish": to_publish,
                "shopify_products": shopify_products,
                "live_error": live_error,
                "orders": (
                    ShopifyOrder.objects
                    .prefetch_related("lines__product")[:50]
                ),
                "manual_changes": (
                    StockMovement.objects
                    .filter(
                        channel=StockMovement.Channel.SHOPIFY,
                        movement_type=StockMovement.MovementType.ADJUSTMENT,
                    )
                    .select_related("product", "batch", "from_location", "to_location")[:50]
                ),
            },
        )

    def post(self, request):
        if request.POST.get("action") == "link":
            return self._link(request)

        try:
            summary = shopify_sync.sync(actor=request.user)
        except (ShopifyError, PermissionDenied, ValidationError) as exc:
            messages.error(request, f"Shopify sync failed: {exc}")
            return redirect("shopify-sync")

        messages.success(
            request,
            f"Synced with Shopify: {summary['orders_imported']} new order(s), "
            f"{summary['lines_posted']} line(s) taken from stock, "
            f"{summary['products_created']} product(s) created on Shopify, "
            f"{summary['products_linked']} linked to existing Shopify products, "
            f"stock set on Shopify for {summary['products_pushed']} product(s).",
        )

        for change in summary["manual_changes"]:
            messages.info(request, f"Changed on Shopify by hand — {change}.")

        if summary["lines_short"] or summary["lines_unlinked"]:
            messages.warning(
                request,
                f"{summary['lines_short'] + summary['lines_unlinked']} Shopify "
                "order line(s) couldn't be taken from stock — see below.",
            )

        for warning in summary["warnings"]:
            messages.warning(request, warning)

        return redirect("shopify-sync")

    def _link(self, request):
        from apps.products.models import Product

        product = Product.objects.filter(pk=request.POST.get("product")).first()
        shopify_product_id = (request.POST.get("shopify_product") or "").strip()

        if product is None or not shopify_product_id:
            messages.error(request, "Pick a Shopify product to link to.")
            return redirect("shopify-sync")

        shopify_sync.link_product(
            actor=request.user,
            product=product,
            shopify_product_id=shopify_product_id,
            how="linked by hand",
        )
        messages.success(
            request,
            f"{product.name} is now linked to that Shopify product. Press Sync now "
            "to set its quantity on Shopify.",
        )
        return redirect("shopify-sync")
