from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.mixins import OwnerRequiredMixin

from .forms import ManufacturerForm
from .models import Manufacturer
from .services import create_manufacturer, update_manufacturer


class ManufacturerListView(OwnerRequiredMixin, ListView):
    template_name = "manufacturers/manufacturer_list.html"
    context_object_name = "manufacturers"

    def get_queryset(self):
        return Manufacturer.objects.for_user(self.request.user)


class ManufacturerCreateView(OwnerRequiredMixin, View):
    template_name = "manufacturers/manufacturer_form.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {"form": ManufacturerForm()},
        )

    def post(self, request):
        form = ManufacturerForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        try:
            manufacturer = create_manufacturer(
                actor=request.user,
                **form.cleaned_data,
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form})

        messages.success(request, "Manufacturer created successfully.")
        return redirect("manufacturer-detail", pk=manufacturer.pk)


class ManufacturerDetailView(OwnerRequiredMixin, DetailView):
    template_name = "manufacturers/manufacturer_detail.html"
    context_object_name = "manufacturer"

    def get_queryset(self):
        return Manufacturer.objects.for_user(self.request.user)


class ManufacturerUpdateView(OwnerRequiredMixin, View):
    template_name = "manufacturers/manufacturer_form.html"

    def get_object(self):
        return (
            Manufacturer.objects.for_user(self.request.user)
            .filter(pk=self.kwargs["pk"])
            .first()
        )

    def get(self, request, pk):
        manufacturer = self.get_object()
        if manufacturer is None:
            raise Http404
        return render(
            request,
            self.template_name,
            {
                "form": ManufacturerForm(instance=manufacturer),
                "object": manufacturer,
            },
        )

    def post(self, request, pk):
        manufacturer = self.get_object()
        if manufacturer is None:
            raise Http404

        form = ManufacturerForm(request.POST, instance=manufacturer)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"form": form, "object": manufacturer},
            )

        try:
            manufacturer = update_manufacturer(
                actor=request.user,
                manufacturer_id=manufacturer.pk,
                **form.cleaned_data,
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return render(
                request,
                self.template_name,
                {"form": form, "object": manufacturer},
            )

        messages.success(request, "Manufacturer updated successfully.")
        return redirect("manufacturer-detail", pk=manufacturer.pk)
