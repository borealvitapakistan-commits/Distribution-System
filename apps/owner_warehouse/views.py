from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.mixins import OwnerRequiredMixin
from apps.owner_inventory.services import (
    get_or_create_unallocated_location,
)

from .forms import AllocateBatchForm, InventoryForm, LocationForm, PlacementFormSet, placement_destinations
from .models import Inventory, Location
from .services import (
    create_inventory,
    create_location,
    location_utilization,
    place_batch,
    update_inventory,
    update_location,
)
from .workspace import pending_for, queue_context, regions_for, selected, warehouses_for


class InventoryListView(OwnerRequiredMixin, ListView):
    template_name = "owner_warehouse/inventory_list.html"
    context_object_name = "inventories"

    def get_queryset(self):
        return regions_for(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        warehouses = warehouses_for(self.request.user)
        pending = pending_for(self.request.user)
        target = selected(warehouses.filter(active=True, inventory__active=True), self.request.GET.get("warehouse"))
        target_region = selected(Inventory.objects.filter(active=True), self.request.GET.get("inventory"))
        if target or target_region:
            region = target.inventory if target else target_region
            pending = pending.filter(Q(location__inventory__isnull=True) | Q(location__inventory=region))
        context.update(queue_context(self.request, pending))
        context.update({
            "warehouse_count": warehouses.count(), "region_count": self.object_list.count(),
            "stored_units": warehouses.aggregate(total=Sum("on_hand", default=0))["total"],
            "target_warehouse": target, "target_inventory": target_region, "inventory_tab": "overview",
        })
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

        messages.success(request, f"{inventory.name} added. Next, add a warehouse in this region.")
        if request.POST.get("next") == "warehouse":
            return redirect(f"{reverse('location-create')}?inventory={inventory.pk}")
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

        messages.success(request, "Region updated successfully.")
        return redirect("inventory-detail", pk=inventory.pk)


class InventoryDetailView(OwnerRequiredMixin, DetailView):
    template_name = "owner_warehouse/inventory_detail.html"
    context_object_name = "inventory"

    def get_queryset(self):
        return regions_for(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        warehouses = warehouses_for(self.request.user).filter(inventory=self.object)
        context["warehouses"] = warehouses
        context.update(queue_context(self.request, pending_for(self.request.user).filter(
            Q(location__inventory=self.object) | Q(location__inventory__isnull=True),
        )))
        context["inventory_tab"] = "overview"
        return context


class AllocateStockView(OwnerRequiredMixin, View):
    template_name = "owner_warehouse/place_stock.html"

    def get_batch(self, request, batch_id):
        from apps.owner_inventory.models import StockBatch
        batch = (
            StockBatch.objects
            .for_user(request.user)
            .select_related("product", "location", "location__inventory")
            .filter(pk=batch_id)
            .first()
        )
        if batch is None:
            raise Http404
        return batch

    def get(self, request, batch_id):
        batch = self.get_batch(request, batch_id)
        return self.show_form(request, batch)

    def destination_options(self, request, batch):
        warehouse = selected(Location.objects.filter(
            active=True, inventory__active=True, location_type=Location.LocationType.OWN,
        ).select_related("inventory"), request.GET.get("warehouse"))
        region = warehouse.inventory if warehouse else selected(
            Inventory.objects.filter(active=True), request.GET.get("inventory"),
        )
        return placement_destinations(batch, region=region)

    def show_form(self, request, batch, formset=None):
        choices, destinations = self.destination_options(request, batch)
        initial = [{"quantity": f"{batch.quantity_remaining.normalize():f}"}]
        warehouse_key = f"warehouse:{request.GET.get('warehouse', '')}"
        warehouse_options = [{"value": key, "warehouse": destination}
                             for key, destination in destinations.items() if isinstance(destination, Location)]
        region_options = [{"value": key, "region": destination}
                          for key, destination in destinations.items() if isinstance(destination, Inventory)]
        if warehouse_key in destinations:
            initial[0]["destination"] = warehouse_key
        elif len(warehouse_options) == 1:
            initial[0]["destination"] = warehouse_options[0]["value"]
        if formset is None:
            formset = PlacementFormSet(
                batch=batch, destinations=destinations, form_kwargs={"choices": choices},
                prefix="placements", initial=initial,
            )
        if batch.location.inventory_id:
            back_url = reverse("inventory-detail", args=[batch.location.inventory_id])
        else:
            back_url = reverse("inventory-list")
        return render(request, self.template_name, {
            "batch": batch, "formset": formset, "has_destinations": bool(destinations),
            "can_place": batch.location.location_type == Location.LocationType.UNALLOCATED and batch.quantity_remaining > 0,
            "today": timezone.localdate(), "back_url": back_url, "inventory_tab": "overview",
            "regions_exist": Inventory.objects.filter(active=True).exists(),
            "warehouse_options": warehouse_options, "region_options": region_options,
        })

    def post(self, request, batch_id):
        batch = self.get_batch(request, batch_id)
        if any(key.startswith(AllocateBatchForm.DEST_PREFIX) for key in request.POST):
            return self.legacy_post(request, batch)
        choices, destinations = self.destination_options(request, batch)
        formset = PlacementFormSet(
            request.POST, batch=batch, destinations=destinations,
            form_kwargs={"choices": choices}, prefix="placements",
        )
        if not formset.is_valid():
            return self.show_form(request, batch, formset)
        try:
            placed = place_batch(actor=request.user, batch_id=batch.pk, allocations=formset.get_allocations())
        except (PermissionDenied, ValidationError) as exc:
            formset._non_form_errors = formset.error_class(getattr(exc, "messages", [str(exc)]))
            batch.refresh_from_db()
            return self.show_form(request, batch, formset)
        total = sum(item.quantity_received for item in placed)
        batch.refresh_from_db()
        if len(placed) == 1 and placed[0].location.location_type == Location.LocationType.OWN:
            message = f"Moved {total.normalize():f} units of {batch.product.name} to {placed[0].location.name}."
        elif len(placed) == 1:
            message = f"Assigned {total.normalize():f} units of {batch.product.name} to {placed[0].location.inventory.name}. Choose its warehouse next."
        else:
            message = f"Moved {total.normalize():f} units of {batch.product.name} to {len(placed)} places."
        if batch.quantity_remaining:
            message += f" {batch.quantity_remaining.normalize():f} units are still waiting to be placed."
        messages.success(request, message)
        if len(placed) == 1:
            destination = placed[0].location
            if destination.location_type == Location.LocationType.OWN:
                return redirect("location-detail", pk=destination.pk)
            return redirect("inventory-detail", pk=destination.inventory_id)
        return self._redirect("inventory-detail", batch)

    def legacy_post(self, request, batch):
        # Preserve existing links/clients while using the same atomic stock checks.
        if batch.location.inventory_id is None:
            destinations = [
                (get_or_create_unallocated_location(actor=request.user, inventory=region), str(region))
                for region in Inventory.objects.filter(active=True)
            ]
        else:
            destinations = [(warehouse, warehouse.name) for warehouse in Location.objects.filter(
                inventory=batch.location.inventory, active=True, location_type=Location.LocationType.OWN,
            )]
        form = AllocateBatchForm(request.POST, destinations=destinations)
        return_url = request.POST.get("return_to") or "inventory-list"
        if not form.is_valid() or not form.get_allocations():
            messages.error(request, "Enter a quantity for at least one destination.")
            return self._redirect(return_url, batch)
        try:
            place_batch(actor=request.user, batch_id=batch.pk, allocations=form.get_allocations())
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
            return self._redirect(return_url, batch)
        messages.success(request, "Stock placed successfully.")
        return self._redirect(return_url, batch)

    def _redirect(self, return_url, batch):
        if return_url == "inventory-detail" and batch.location.inventory_id:
            return redirect("inventory-detail", pk=batch.location.inventory_id)

        return redirect("inventory-list")


class LocationListView(OwnerRequiredMixin, ListView):
    template_name = "owner_warehouse/location_list.html"
    context_object_name = "locations"

    def get_queryset(self):
        # Only real warehouses. Transit and the unallocated pools are the
        # system's own holding places (unallocated stock is shown on the
        # Inventory page), and legacy rows of retired types stay hidden.
        warehouses = warehouses_for(self.request.user)
        search = self.request.GET.get("q", "").strip()
        if search:
            warehouses = warehouses.filter(Q(name__icontains=search) | Q(code__icontains=search) | Q(inventory__name__icontains=search))
        region = selected(Inventory.objects.all(), self.request.GET.get("region"))
        if region:
            warehouses = warehouses.filter(inventory=region)
        status = self.request.GET.get("status")
        if status == "sellable":
            warehouses = warehouses.filter(active=True, is_sellable=True)
        elif status == "held":
            warehouses = warehouses.filter(active=True, is_sellable=False)
        elif status == "inactive":
            warehouses = warehouses.filter(active=False)
        return warehouses

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            "regions": Inventory.objects.for_user(self.request.user), "inventory_tab": "warehouses",
            "search": self.request.GET.get("q", ""), "selected_region": self.request.GET.get("region", ""),
            "selected_status": self.request.GET.get("status", ""),
        })
        return context


class LocationCreateView(OwnerRequiredMixin, View):
    template_name = "owner_warehouse/location_form.html"

    def get(self, request):
        initial_inventory = selected(Inventory.objects.filter(active=True), request.GET.get("inventory"))

        return render(
            request,
            self.template_name,
            {
                "form": LocationForm(initial_inventory=initial_inventory),
                "selected_inventory": initial_inventory,
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
            f"{location.name} added. You can now place stock in this warehouse.",
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
            "Warehouse updated successfully.",
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
            .select_related("manufacturer", "inventory")
        )

    def get_context_data(self, **kwargs):
        from apps.owner_inventory.models import StockBatch

        context = super().get_context_data(**kwargs)

        context["utilization"] = location_utilization(
            self.object
        )

        batches = (
            StockBatch.objects
            .filter(location=self.object)
            .select_related("product")
            .available()
            .fefo_ordered()
        )
        context["stock_summary"] = batches.aggregate(
            product_count=Count("product", distinct=True), batch_count=Count("pk"),
        )
        search = self.request.GET.get("q", "").strip()
        if search:
            batches = batches.filter(Q(product__name__icontains=search) | Q(product__sku__icontains=search) | Q(batch_number__icontains=search))
        context.update({"batches": batches, "search": search, "today": timezone.localdate(), "inventory_tab": "warehouses"})
        return context
