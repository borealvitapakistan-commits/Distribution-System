from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import ListView

from apps.accounts.mixins import OwnerRequiredMixin
from apps.batches.models import Batch
from apps.batches.services import trace_batch_code

from .forms import GiveToDistributorForm, ReceiveStockForm
from .models import StockBatch, StockMovement
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
        return (
            StockMovement.objects
            .for_user(self.request.user)
            .select_related(
                "product", "from_location", "to_location", "created_by"
            )
        )


class ReceiveStockView(OwnerRequiredMixin, View):
    template_name = "owner_inventory/receive_stock_form.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {"form": ReceiveStockForm()},
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
