from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.mixins import DistributorRequiredMixin
from apps.distributor_inventory.services import (
    get_or_create_unallocated_location,
    reallocate_batch,
    unallocated_batches,
)

from .forms import AllocateDistributorBatchForm, DistributorInventoryForm, DistributorLocationForm
from .models import DistributorInventory, DistributorLocation
from .services import (
    create_inventory,
    create_location,
    location_utilization,
    update_inventory,
    update_location,
)


class DistributorInventoryListView(DistributorRequiredMixin, ListView):
    template_name = "distributor_warehouse/inventory_list.html"
    context_object_name = "inventories"

    def get_queryset(self):
        return DistributorInventory.objects.for_user(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        profile = self.request.user.distributor_profile

        destinations = [
            (
                get_or_create_unallocated_location(
                    actor=self.request.user,
                    distributor_profile=profile,
                    distributor_inventory=inventory,
                ),
                str(inventory),
            )
            for inventory in DistributorInventory.objects.filter(
                distributor_profile=profile, active=True
            )
        ]

        context["unallocated_rows"] = [
            (batch, AllocateDistributorBatchForm(destinations=destinations))
            for batch in (
                unallocated_batches(distributor_profile=profile)
                .select_related("product")
            )
        ]

        return context


class DistributorInventoryCreateView(DistributorRequiredMixin, View):
    template_name = "distributor_warehouse/inventory_form.html"

    def get(self, request):
        return render(request, self.template_name, {"form": DistributorInventoryForm()})

    def post(self, request):
        form = DistributorInventoryForm(request.POST)

        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        try:
            inventory = create_inventory(actor=request.user, **form.cleaned_data)
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form})

        messages.success(request, "Region created successfully.")
        return redirect("distributor-region-detail", pk=inventory.pk)


class DistributorInventoryUpdateView(DistributorRequiredMixin, View):
    template_name = "distributor_warehouse/inventory_form.html"

    def get_object(self):
        return (
            DistributorInventory.objects.for_user(self.request.user)
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
            {"form": DistributorInventoryForm(instance=inventory), "object": inventory},
        )

    def post(self, request, pk):
        inventory = self.get_object()
        if inventory is None:
            raise Http404

        form = DistributorInventoryForm(request.POST, instance=inventory)
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

        messages.success(request, "Region updated successfully.")
        return redirect("distributor-region-detail", pk=inventory.pk)


class DistributorInventoryDetailView(DistributorRequiredMixin, DetailView):
    template_name = "distributor_warehouse/inventory_detail.html"
    context_object_name = "inventory"

    def get_queryset(self):
        return DistributorInventory.objects.for_user(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        profile = self.request.user.distributor_profile

        warehouses = DistributorLocation.objects.filter(
            distributor_inventory=self.object,
            location_type=DistributorLocation.LocationType.WAREHOUSE,
        )
        context["warehouses"] = warehouses

        destinations = [
            (warehouse, warehouse.name) for warehouse in warehouses.filter(active=True)
        ]

        context["unallocated_rows"] = [
            (batch, AllocateDistributorBatchForm(destinations=destinations))
            for batch in (
                unallocated_batches(
                    distributor_profile=profile, distributor_inventory=self.object
                ).select_related("product")
            )
        ]

        return context


class DistributorAllocateStockView(DistributorRequiredMixin, View):
    def post(self, request, batch_id):
        from apps.distributor_inventory.models import DistributorStockBatch

        profile = request.user.distributor_profile

        batch = (
            DistributorStockBatch.objects
            .for_user(request.user)
            .select_related("location", "location__distributor_inventory")
            .filter(pk=batch_id)
            .first()
        )

        if batch is None:
            raise Http404

        return_url = request.POST.get("return_to") or "distributor-region-list"

        if batch.location.distributor_inventory_id is None:
            destinations = [
                (
                    get_or_create_unallocated_location(
                        actor=request.user,
                        distributor_profile=profile,
                        distributor_inventory=inventory,
                    ),
                    str(inventory),
                )
                for inventory in DistributorInventory.objects.filter(
                    distributor_profile=profile, active=True
                )
            ]
        else:
            warehouses = DistributorLocation.objects.filter(
                distributor_inventory=batch.location.distributor_inventory,
                location_type=DistributorLocation.LocationType.WAREHOUSE,
                active=True,
            )
            destinations = [(warehouse, warehouse.name) for warehouse in warehouses]

        form = AllocateDistributorBatchForm(request.POST, destinations=destinations)

        if not form.is_valid() or not form.get_allocations():
            messages.error(request, "Enter a quantity for at least one destination.")
            return self._redirect(return_url, batch)

        try:
            for destination, quantity in form.get_allocations():
                reallocate_batch(
                    actor=request.user,
                    distributor_profile=profile,
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
        if return_url == "distributor-region-detail" and batch.location.distributor_inventory_id:
            return redirect("distributor-region-detail", pk=batch.location.distributor_inventory_id)

        return redirect("distributor-region-list")


class DistributorLocationListView(DistributorRequiredMixin, ListView):
    template_name = "distributor_warehouse/location_list.html"
    context_object_name = "locations"

    def get_queryset(self):
        return (
            DistributorLocation.objects
            .for_user(self.request.user)
            .select_related("distributor_inventory")
            .filter(location_type=DistributorLocation.LocationType.WAREHOUSE)
        )


class DistributorLocationCreateView(DistributorRequiredMixin, View):
    template_name = "distributor_warehouse/location_form.html"

    def get(self, request):
        profile = request.user.distributor_profile

        initial_inventory = (
            DistributorInventory.objects
            .filter(pk=request.GET.get("inventory"), distributor_profile=profile)
            .first()
        )

        return render(
            request,
            self.template_name,
            {
                "form": DistributorLocationForm(
                    distributor_profile=profile,
                    initial_inventory=initial_inventory,
                )
            },
        )

    def post(self, request):
        profile = request.user.distributor_profile
        form = DistributorLocationForm(request.POST, distributor_profile=profile)

        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        try:
            location = create_location(actor=request.user, **form.cleaned_data)
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form})

        messages.success(request, "Warehouse created successfully.")
        return redirect("distributor-warehouse-detail", pk=location.pk)


class DistributorLocationUpdateView(DistributorRequiredMixin, View):
    template_name = "distributor_warehouse/location_form.html"

    def get_object(self):
        return (
            DistributorLocation.objects
            .for_user(self.request.user)
            .filter(pk=self.kwargs["pk"])
            .first()
        )

    def get(self, request, pk):
        location = self.get_object()
        if location is None:
            raise Http404

        return render(
            request,
            self.template_name,
            {
                "form": DistributorLocationForm(
                    instance=location,
                    distributor_profile=request.user.distributor_profile,
                ),
                "object": location,
            },
        )

    def post(self, request, pk):
        location = self.get_object()
        if location is None:
            raise Http404

        form = DistributorLocationForm(
            request.POST,
            instance=location,
            distributor_profile=request.user.distributor_profile,
        )

        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"form": form, "object": location},
            )

        try:
            updated = update_location(
                actor=request.user,
                location_id=location.pk,
                **form.cleaned_data,
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return render(
                request,
                self.template_name,
                {"form": form, "object": location},
            )

        messages.success(request, "Warehouse updated successfully.")
        return redirect("distributor-warehouse-detail", pk=updated.pk)


class DistributorLocationDetailView(DistributorRequiredMixin, DetailView):
    template_name = "distributor_warehouse/location_detail.html"
    context_object_name = "location"

    def get_queryset(self):
        return DistributorLocation.objects.for_user(self.request.user)

    def get_context_data(self, **kwargs):
        from apps.distributor_inventory.models import DistributorStockBatch

        context = super().get_context_data(**kwargs)

        context["utilization"] = location_utilization(self.object)

        context["batches"] = (
            DistributorStockBatch.objects
            .filter(location=self.object)
            .select_related("product")
            .available()
            .fefo_ordered()
        )

        return context
