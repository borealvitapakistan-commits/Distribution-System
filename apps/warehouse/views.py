from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.mixins import OwnerRequiredMixin

from .forms import LocationForm
from .models import Location
from .services import (
    create_location,
    location_utilization,
    update_location,
)


class LocationListView(OwnerRequiredMixin, ListView):
    template_name = "warehouse/location_list.html"
    context_object_name = "locations"

    def get_queryset(self):
        return (
            Location.objects
            .for_user(self.request.user)
            .select_related("manufacturer", "distributor_profile", "customer")
        )


class LocationCreateView(OwnerRequiredMixin, View):
    template_name = "warehouse/location_form.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {
                "form": LocationForm()
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
    template_name = "warehouse/location_form.html"

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
    template_name = "warehouse/location_detail.html"
    context_object_name = "location"

    def get_queryset(self):
        return (
            Location.objects
            .for_user(self.request.user)
            .select_related("manufacturer", "distributor_profile", "customer")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["utilization"] = location_utilization(
            self.object
        )

        return context
