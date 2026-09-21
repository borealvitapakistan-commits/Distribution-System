from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import ListView

from apps.accounts.mixins import DistributorRequiredMixin

from .forms import DistributorReceiveStockForm
from .models import DistributorStockBalance, DistributorStockBatch, DistributorStockMovement
from .services import receive_stock


def _attach_batch_codes(balances):
    """A DistributorStockBalance is a summed total — it can be fed by
    more than one batch at the same product/location — so the batch
    code(s) behind it are computed, not a direct field."""
    balances = list(balances)

    for balance in balances:
        balance.batch_codes = list(
            DistributorStockBatch.objects
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


class DistributorStockBalanceListView(DistributorRequiredMixin, ListView):
    template_name = "distributor_inventory/stock_balance_list.html"
    context_object_name = "balances"

    def get_queryset(self):
        return (
            DistributorStockBalance.objects
            .for_user(self.request.user)
            .select_related("product", "location")
            .filter(quantity__gt=0)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["balances"] = _attach_batch_codes(context["balances"])
        return context


class DistributorStockBatchListView(DistributorRequiredMixin, ListView):
    template_name = "distributor_inventory/stock_batch_list.html"
    context_object_name = "batches"

    def get_queryset(self):
        return (
            DistributorStockBatch.objects
            .for_user(self.request.user)
            .select_related("product", "location")
            .available()
            .fefo_ordered()
        )


class DistributorStockMovementListView(DistributorRequiredMixin, ListView):
    template_name = "distributor_inventory/stock_movement_list.html"
    context_object_name = "movements"
    paginate_by = 50

    def get_queryset(self):
        return (
            DistributorStockMovement.objects
            .for_user(self.request.user)
            .select_related("product", "from_location", "to_location", "created_by")
        )


class DistributorReceiveStockView(DistributorRequiredMixin, View):
    template_name = "distributor_inventory/receive_stock_form.html"

    def get(self, request):
        profile = request.user.distributor_profile
        return render(
            request,
            self.template_name,
            {"form": DistributorReceiveStockForm(distributor_profile=profile)},
        )

    def post(self, request):
        profile = request.user.distributor_profile
        form = DistributorReceiveStockForm(request.POST, distributor_profile=profile)

        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        try:
            receive_stock(
                actor=request.user,
                distributor_profile=profile,
                product=form.cleaned_data["product"],
                quantity=form.cleaned_data["quantity"],
                to_location=form.cleaned_data["to_location"] or None,
                reference=form.cleaned_data["reference"],
                received_date=form.cleaned_data["received_date"],
                expiry_date=form.cleaned_data["expiry_date"],
                batch_number=form.cleaned_data["batch_number"],
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form})

        messages.success(request, "Stock received successfully.")
        return redirect("distributor-stock-balance-list")
