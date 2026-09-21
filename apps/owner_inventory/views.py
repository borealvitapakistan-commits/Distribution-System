from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import ListView

from apps.accounts.mixins import OwnerRequiredMixin

from .forms import GiveToDistributorForm, ReceiveStockForm
from .models import StockBalance, StockBatch, StockMovement
from .services import give_to_distributor, receive_stock


def _attach_batch_codes(balances):
    """A StockBalance is a summed total — it can be fed by more than one
    batch at the same product/location — so the batch code(s) behind it
    are computed, not a direct field."""
    balances = list(balances)

    for balance in balances:
        balance.batch_codes = list(
            StockBatch.objects
            .filter(
                product=balance.product,
                location=balance.location,
                quantity_remaining__gt=0,
            )
            .exclude(batch_number="")
            .values_list("batch_number", flat=True)
            .distinct()
        )

    return balances


class StockBalanceListView(OwnerRequiredMixin, ListView):
    template_name = "owner_inventory/stock_balance_list.html"
    context_object_name = "balances"

    def get_queryset(self):
        return (
            StockBalance.objects
            .for_user(self.request.user)
            .select_related("product", "location")
            .filter(quantity__gt=0)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["balances"] = _attach_batch_codes(context["balances"])
        return context


class StockBatchListView(OwnerRequiredMixin, ListView):
    template_name = "owner_inventory/stock_batch_list.html"
    context_object_name = "batches"

    def get_queryset(self):
        return (
            StockBatch.objects
            .for_user(self.request.user)
            .select_related("product", "location")
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
