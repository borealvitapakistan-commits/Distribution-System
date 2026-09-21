from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.mixins import OwnerRequiredMixin
from apps.owner_inventory.services import (
    get_or_create_unallocated_location,
    reallocate_batch,
    unallocated_batches,
)

from .forms import AllocateBatchForm, InventoryForm, LocationForm
from .models import Inventory, Location
from .services import (
    create_inventory,
    create_location,
    location_utilization,
    update_inventory,
    update_location,
)


class InventoryListView(OwnerRequiredMixin, ListView):
    template_name = "owner_warehouse/inventory_list.html"
    context_object_name = "inventories"

    def get_queryset(self):
        return Inventory.objects.for_user(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        destinations = [
            (
                get_or_create_unallocated_location(
                    actor=self.request.user, inventory=inventory
                ),
                str(inventory),
            )
            for inventory in Inventory.objects.filter(active=True)
        ]

        context["unallocated_rows"] = [
            (batch, AllocateBatchForm(destinations=destinations))
            for batch in unallocated_batches().select_related("product")
        ]

        return context


class InventoryCreateView(OwnerRequiredMixin, View):
    template_name = "owner_warehouse/inventory_form.html"

    def get(self, request):
        return render(request, self.template_name, {"form": InventoryForm()})

    def post(self, request):
        form = InventoryForm(request.POST)

        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        try:
            inventory = create_inventory(actor=request.user, **form.cleaned_data)
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form})

        messages.success(request, "Inventory created successfully.")
        return redirect("inventory-detail", pk=inventory.pk)


class InventoryUpdateView(OwnerRequiredMixin, View):
    template_name = "owner_warehouse/inventory_form.html"

    def get_object(self):
        return (
            Inventory.objects.for_user(self.request.user)
            .filter(pk=self.kwargs["pk"])
            .first()
        )

    def get(self, request, pk):
        inventory = self.get_object()
        if inventory is None:
            raise Http404
        return render(
            request,
            self.template_name,
            {"form": InventoryForm(instance=inventory), "object": inventory},
        )

    def post(self, request, pk):
        inventory = self.get_object()
        if inventory is None:
            raise Http404

        form = InventoryForm(request.POST, instance=inventory)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"form": form, "object": inventory},
            )

        try:
            inventory = update_inventory(
                actor=request.user,
                inventory_id=inventory.pk,
                **form.cleaned_data,
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return render(
                request,
                self.template_name,
                {"form": form, "object": inventory},
            )

        messages.success(request, "Inventory updated successfully.")
        return redirect("inventory-detail", pk=inventory.pk)


class InventoryDetailView(OwnerRequiredMixin, DetailView):
    template_name = "owner_warehouse/inventory_detail.html"
    context_object_name = "inventory"

    def get_queryset(self):
        return Inventory.objects.for_user(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        warehouses = Location.objects.filter(
            inventory=self.object,
            location_type=Location.LocationType.OWN,
        )
        context["warehouses"] = warehouses

        destinations = [
            (warehouse, warehouse.name) for warehouse in warehouses.filter(active=True)
        ]

        context["unallocated_rows"] = [
            (batch, AllocateBatchForm(destinations=destinations))
            for batch in (
                unallocated_batches(inventory=self.object).select_related("product")
            )
        ]

        return context


class AllocateStockView(OwnerRequiredMixin, View):
    def post(self, request, batch_id):
        from apps.owner_inventory.models import StockBatch

        batch = (
            StockBatch.objects
            .for_user(request.user)
            .select_related("location", "location__inventory")
            .filter(pk=batch_id)
            .first()
        )

        if batch is None:
            raise Http404

        return_url = request.POST.get("return_to") or "inventory-list"

        if batch.location.inventory_id is None:
            destinations = [
                (
                    get_or_create_unallocated_location(
                        actor=request.user, inventory=inventory
                    ),
                    str(inventory),
                )
                for inventory in Inventory.objects.filter(active=True)
            ]
        else:
            warehouses = Location.objects.filter(
                inventory=batch.location.inventory,
                location_type=Location.LocationType.OWN,
                active=True,
            )
            destinations = [(warehouse, warehouse.name) for warehouse in warehouses]

        form = AllocateBatchForm(request.POST, destinations=destinations)

        if not form.is_valid() or not form.get_allocations():
            messages.error(request, "Enter a quantity for at least one destination.")
            return self._redirect(return_url, batch)

        try:
            for destination, quantity in form.get_allocations():
                reallocate_batch(
                    actor=request.user,
                    batch=batch,
                    quantity=quantity,
                    destination_location=destination,
                )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return self._redirect(return_url, batch)

        messages.success(request, "Stock allocated successfully.")
        return self._redirect(return_url, batch)

    def _redirect(self, return_url, batch):
        if return_url == "inventory-detail" and batch.location.inventory_id:
            return redirect("inventory-detail", pk=batch.location.inventory_id)

        return redirect("inventory-list")


class LocationListView(OwnerRequiredMixin, ListView):
    template_name = "owner_warehouse/location_list.html"
    context_object_name = "locations"

    def get_queryset(self):
        return (
            Location.objects
            .for_user(self.request.user)
            .select_related("inventory", "manufacturer", "customer")
        )


class LocationCreateView(OwnerRequiredMixin, View):
    template_name = "owner_warehouse/location_form.html"

    def get(self, request):
        initial_inventory = (
            Inventory.objects
            .filter(pk=request.GET.get("inventory"))
            .first()
        )

        return render(
            request,
            self.template_name,
            {
                "form": LocationForm(initial_inventory=initial_inventory)
            },
        )

    def post(self, request):
        form = LocationForm(
            request.POST,
        )

        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"form": form},
            )

        try:
            location = create_location(
                actor=request.user,
                **form.cleaned_data,
            )
        except (
            PermissionDenied,
            ValidationError,
        ) as exc:
            form.add_error(None, exc)
            return render(
                request,
                self.template_name,
                {"form": form},
            )

        messages.success(
            request,
            "Location created successfully.",
        )

        return redirect(
            "location-detail",
            pk=location.pk,
        )


class LocationUpdateView(OwnerRequiredMixin, View):
    template_name = "owner_warehouse/location_form.html"

    def get_object(self):
        return (
            Location.objects
            .for_user(self.request.user)
            .filter(pk=self.kwargs["pk"])
            .first()
        )

    def get(self, request, pk):
        location = self.get_object()

        if location is None:
            raise Http404

        if location.location_type not in LocationForm.MANUAL_LOCATION_TYPES_SET:
            messages.error(
                request,
                "This location is managed automatically by the system "
                "and can't be edited here.",
            )
            return redirect("location-detail", pk=location.pk)

        return render(
            request,
            self.template_name,
            {
                "form": LocationForm(
                    instance=location,
                ),
                "object": location,
            },
        )

    def post(self, request, pk):
        location = self.get_object()

        if location is None:
            raise Http404

        if location.location_type not in LocationForm.MANUAL_LOCATION_TYPES_SET:
            messages.error(
                request,
                "This location is managed automatically by the system "
                "and can't be edited here.",
            )
            return redirect("location-detail", pk=location.pk)

        form = LocationForm(
            request.POST,
            instance=location,
        )

        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "object": location,
                },
            )

        try:
            updated = update_location(
                actor=request.user,
                location_id=location.pk,
                **form.cleaned_data,
            )
        except (
            PermissionDenied,
            ValidationError,
        ) as exc:
            form.add_error(None, exc)
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "object": location,
                },
            )

        messages.success(
            request,
            "Location updated successfully.",
        )

        return redirect(
            "location-detail",
            pk=updated.pk,
        )


class LocationDetailView(OwnerRequiredMixin, DetailView):
    template_name = "owner_warehouse/location_detail.html"
    context_object_name = "location"

    def get_queryset(self):
        return (
            Location.objects
            .for_user(self.request.user)
            .select_related("manufacturer", "customer")
        )

    def get_context_data(self, **kwargs):
        from apps.owner_inventory.models import StockBalance, StockBatch

        context = super().get_context_data(**kwargs)

        context["utilization"] = location_utilization(
            self.object
        )

        balances = list(
            StockBalance.objects
            .filter(location=self.object, quantity__gt=0)
            .select_related("product")
            .order_by("product__name")
        )

        for balance in balances:
            balance.batch_codes = list(
                StockBatch.objects
                .filter(
                    product=balance.product,
                    location=self.object,
                    quantity_remaining__gt=0,
                )
                .exclude(batch_number="")
                .values_list("batch_number", flat=True)
                .distinct()
            )

        context["balances"] = balances

        return context
