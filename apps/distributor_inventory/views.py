from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.mixins import DistributorRequiredMixin

from .forms import DistributorReceiveStockForm, SalePaymentProofForm, SubDistributorSaleForm
from .models import (
    DistributorStockBatch,
    DistributorStockMovement,
    SubDistributorSale,
)
from .services import (
    attach_sale_payment_proof,
    receive_stock,
    sell_batches_to_sub_distributor,
)


def _batch_history(sale):
    """The sold batch's own story in this Distributor's ledger — how it
    arrived, where it was allocated, and everything else drawn from it.
    Every split of one lot across warehouses keeps its batch number, so
    that is what ties the pieces together."""
    batch = sale.batch

    if batch is None:
        return []

    movements = DistributorStockMovement.objects.filter(
        distributor_profile=sale.distributor_profile
    )

    if batch.batch_number:
        movements = movements.filter(batch__batch_number=batch.batch_number)
    else:
        movements = movements.filter(batch=batch)

    return movements.select_related(
        "from_location", "to_location", "batch", "created_by"
    ).order_by("created_at")


class DistributorStockBalanceListView(DistributorRequiredMixin, ListView):
    """Stock on hand, one row per batch — each lot keeps its own
    quantity and expiry rather than being summed per product."""

    template_name = "distributor_inventory/stock_balance_list.html"
    context_object_name = "batches"

    def get_queryset(self):
        return (
            DistributorStockBatch.objects
            .for_user(self.request.user)
            .select_related("product", "location")
            .available()
            .fefo_ordered()
        )


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


class SubDistributorSaleListView(DistributorRequiredMixin, ListView):
    template_name = "distributor_inventory/sale_list.html"
    context_object_name = "sales"
    paginate_by = 50

    def get_queryset(self):
        return (
            SubDistributorSale.objects
            .for_user(self.request.user)
            .select_related(
                "product",
                "from_location",
                "from_location__distributor_inventory",
                "batch",
                "created_by",
            )
        )


class SubDistributorSaleDetailView(DistributorRequiredMixin, DetailView):
    template_name = "distributor_inventory/sale_detail.html"
    context_object_name = "sale"

    def get_queryset(self):
        return (
            SubDistributorSale.objects
            .for_user(self.request.user)
            .select_related(
                "product",
                "from_location",
                "from_location__distributor_inventory",
                "batch",
                "created_by",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["batch_movements"] = _batch_history(self.object)
        context["proof_form"] = SalePaymentProofForm()
        return context


class SubDistributorSalePaymentProofView(DistributorRequiredMixin, View):
    def post(self, request, pk):
        sale = SubDistributorSale.objects.for_user(request.user).filter(pk=pk).first()

        if sale is None:
            raise Http404

        form = SalePaymentProofForm(request.POST, request.FILES)

        if not form.is_valid():
            messages.error(request, "Choose a payment proof file to upload.")
            return redirect("distributor-sale-detail", pk=pk)

        try:
            attach_sale_payment_proof(
                actor=request.user,
                distributor_profile=request.user.distributor_profile,
                sale=sale,
                proof=form.cleaned_data["proof"],
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("distributor-sale-detail", pk=pk)

        messages.success(request, "Payment proof added.")
        return redirect("distributor-sale-detail", pk=pk)


class SubDistributorSaleCreateView(DistributorRequiredMixin, View):
    template_name = "distributor_inventory/sale_form.html"

    def _render(self, request, form):
        return render(request, self.template_name, {"form": form})

    def get(self, request):
        profile = request.user.distributor_profile
        return self._render(request, SubDistributorSaleForm(distributor_profile=profile))

    def post(self, request):
        profile = request.user.distributor_profile
        form = SubDistributorSaleForm(request.POST, request.FILES, distributor_profile=profile)

        if not form.is_valid():
            return self._render(request, form)

        try:
            sales = sell_batches_to_sub_distributor(
                actor=request.user,
                distributor_profile=profile,
                sub_distributor_name=form.cleaned_data["sub_distributor_name"],
                allocations=form.allocations,
                sale_date=form.cleaned_data["sale_date"],
                note=form.cleaned_data["note"],
                payment_proof=form.cleaned_data["payment_proof"],
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return self._render(request, form)

        if len(sales) == 1:
            messages.success(request, "Sale to sub-distributor recorded.")
            return redirect("distributor-sale-detail", pk=sales[0].pk)

        messages.success(
            request,
            f"Sale to {sales[0].sub_distributor_name} recorded from {len(sales)} batches.",
        )
        return redirect("distributor-sale-list")
