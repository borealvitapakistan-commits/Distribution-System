from django.contrib import messages
from django.contrib.auth.decorators import (
    login_required,
)
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import (
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)

from apps.accounts.mixins import OwnerRequiredMixin
from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.audit.services import record_audit_event
from apps.manufacturers.models import Manufacturer, ManufacturerOrder
from apps.products.models import Product

from .context_processors import current_brand
from .forms import BrandForm
from .models import Brand


def brand_logo(request, pk):
    """Public branding is also needed on sign-in pages, with DEBUG off."""
    brand = current_brand()
    if brand is None or brand.pk != pk:
        brand = get_object_or_404(Brand, pk=pk)
    if not brand.logo:
        raise Http404("No brand logo has been uploaded.")
    try:
        return FileResponse(brand.logo.open("rb"))
    except FileNotFoundError as exc:
        raise Http404("The brand logo could not be found.") from exc


@login_required
def dashboard_redirect(request):
    user = request.user

    if not user.is_active:
        raise PermissionDenied(
            "An active account is required."
        )

    if user.is_owner:
        return redirect("owner-dashboard")

    raise PermissionDenied(
        "Your user role is invalid."
    )


class OwnerDashboardView(OwnerRequiredMixin, TemplateView):
    template_name = "dashboards/owner.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(
            **kwargs
        )

        context["owner_count"] = User.objects.filter(
            role=User.Role.OWNER,
            is_active=True,
        ).count()

        context["manufacturer_count"] = Manufacturer.objects.count()
        context["product_count"] = Product.objects.count()

        context["audit_count"] = (
            AuditEvent.objects
            .for_user(self.request.user)
            .count()
        )

        context["recent_orders"] = (
            ManufacturerOrder.objects.for_user(self.request.user)
            .select_related("manufacturer", "brand")
            .order_by("-created_at")[:4]
        )
        context["recent_events"] = (
            AuditEvent.objects.for_user(self.request.user)
            .select_related("actor")[:5]
        )

        return context


class BrandListView(OwnerRequiredMixin, ListView):
    template_name = "core/brand_list.html"
    context_object_name = "brands"

    def get_queryset(self):
        return Brand.objects.for_user(self.request.user)


class BrandCreateView(OwnerRequiredMixin, View):
    template_name = "core/brand_form.html"

    def get(self, request):
        return render(request, self.template_name, {"form": BrandForm()})

    def post(self, request):
        form = BrandForm(request.POST, request.FILES)

        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        brand = form.save()

        record_audit_event(
            user=request.user,
            action="brand.created",
            instance=brand,
            after_data={
                field: str(getattr(brand, field, ""))
                for field in form.changed_data
            },
            request=request,
        )

        messages.success(request, "Brand created successfully.")
        return redirect("brand-detail", pk=brand.pk)


class BrandDetailView(OwnerRequiredMixin, DetailView):
    model = Brand
    template_name = "core/brand_detail.html"
    context_object_name = "brand_obj"

    def get_queryset(self):
        return Brand.objects.for_user(self.request.user)


class BrandUpdateView(OwnerRequiredMixin, UpdateView):
    model = Brand
    form_class = BrandForm
    template_name = "core/brand_form.html"
    context_object_name = "brand_obj"

    def get_queryset(self):
        return Brand.objects.for_user(self.request.user)

    def get_success_url(self):
        return reverse("brand-detail", kwargs={"pk": self.object.pk})

    def form_valid(self, form):
        changed_fields = list(form.changed_data)

        before_data = {
            field: str(
                getattr(
                    self.object,
                    field,
                    "",
                )
            )
            for field in changed_fields
        }

        response = super().form_valid(form)

        after_data = {
            field: str(
                getattr(
                    self.object,
                    field,
                    "",
                )
            )
            for field in changed_fields
        }

        if changed_fields:
            record_audit_event(
                user=self.request.user,
                action="brand.updated",
                instance=self.object,
                before_data=before_data,
                after_data=after_data,
                request=self.request,
            )

        messages.success(self.request, "Brand updated successfully.")

        return response
