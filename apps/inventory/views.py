from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import ListView

from apps.accounts.mixins import (
    DistributorRequiredMixin,
    OwnerRequiredMixin,
)

from .forms import GiveToDistributorForm, ReceiveStockForm
from .models import StockBalance, StockMovement
from .services import give_to_distributor, receive_stock


class InventoryListView(OwnerRequiredMixin, ListView):
    template_name = "inventory/inventory_list.html"
    context_object_name = "balances"

    def get_queryset(self):
        return (
            StockBalance.objects
            .for_user(self.request.user)
            .select_related("product", "location")
            .filter(quantity__gt=0)
        )


class StockMovementListView(OwnerRequiredMixin, ListView):
    template_name = "inventory/stock_movement_list.html"
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
    template_name = "inventory/receive_stock_form.html"

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
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form})

        messages.success(request, "Inventory received successfully.")
        return redirect("inventory-list")


class GiveToDistributorView(OwnerRequiredMixin, View):
    template_name = "inventory/give_to_distributor_form.html"

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
            give_to_distributor(
                actor=request.user,
                product=form.cleaned_data["product"],
                quantity=form.cleaned_data["quantity"],
                from_location=form.cleaned_data["from_location"],
                distributor_profile=form.cleaned_data["distributor"],
                reference=form.cleaned_data["reference"],
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form})

        messages.success(request, "Stock sent to Distributor successfully.")
        return redirect("inventory-list")


class DistributorInventoryListView(DistributorRequiredMixin, ListView):
    template_name = "inventory/distributor_inventory_list.html"
    context_object_name = "balances"

    def get_queryset(self):
        return (
            StockBalance.objects
            .for_user(self.request.user)
            .select_related("product")
            .filter(quantity__gt=0)
        )
