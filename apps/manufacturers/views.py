from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.mixins import OwnerRequiredMixin
from apps.core.utils import render_pdf

from .forms import (
    ManufacturerForm,
    ManufacturerOrderForm,
    ManufacturerOrderInvoiceForm,
    ManufacturerOrderItemFormSet,
    ManufacturerOrderOutcomeForm,
    ManufacturerOrderPaymentForm,
)
from .models import Manufacturer, ManufacturerOrder
from .services import (
    create_manufacturer,
    create_manufacturer_order,
    mark_manufacturer_order_received,
    record_manufacturer_invoice,
    record_manufacturer_payment,
    set_manufacturer_order_outcome,
    update_manufacturer,
)


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


def manufacturer_order_item_rows(formset):
    rows = []

    for form in formset.forms:
        if not form.cleaned_data:
            continue

        if form.cleaned_data.get("DELETE"):
            continue

        if not form.cleaned_data.get("product"):
            continue

        rows.append(
            {
                "product": form.cleaned_data["product"],
                "quantity": form.cleaned_data["quantity"],
                "unit_price": form.cleaned_data["unit_price"],
                "source_purchase_order_item": form.cleaned_data.get(
                    "source_purchase_order_item"
                ),
            }
        )

    return rows


class ManufacturerOrderListView(OwnerRequiredMixin, ListView):
    template_name = "manufacturers/manufacturer_order_list.html"
    context_object_name = "orders"

    def get_queryset(self):
        return (
            ManufacturerOrder.objects
            .for_user(self.request.user)
            .select_related("manufacturer", "brand")
            .prefetch_related("items")
        )


class ManufacturerOrderCreateView(OwnerRequiredMixin, View):
    template_name = "manufacturers/manufacturer_order_form.html"

    def get(self, request):
        formset_kwargs = {"instance": ManufacturerOrder()}

        product_id = request.GET.get("product")
        if product_id:
            formset_kwargs["initial"] = [
                {
                    "product": product_id,
                    "quantity": request.GET.get("quantity") or "",
                    "source_purchase_order_item": request.GET.get("source_item") or "",
                }
            ]

        return render(
            request,
            self.template_name,
            {
                "form": ManufacturerOrderForm(),
                "formset": ManufacturerOrderItemFormSet(**formset_kwargs),
            },
        )

    def post(self, request):
        form = ManufacturerOrderForm(request.POST)
        formset = ManufacturerOrderItemFormSet(
            request.POST,
            instance=ManufacturerOrder(),
        )

        if not form.is_valid() or not formset.is_valid():
            return render(
                request,
                self.template_name,
                {"form": form, "formset": formset},
            )

        try:
            order = create_manufacturer_order(
                actor=request.user,
                manufacturer=form.cleaned_data["manufacturer"],
                brand=form.cleaned_data["brand"],
                items=manufacturer_order_item_rows(formset),
            )
        except (PermissionDenied, ValidationError) as exc:
            return render(
                request,
                self.template_name,
                {"form": form, "formset": formset, "service_error": exc},
            )

        messages.success(request, "Manufacturer order created successfully.")
        return redirect("manufacturer-order-detail", pk=order.pk)


class ManufacturerOrderDetailView(OwnerRequiredMixin, DetailView):
    template_name = "manufacturers/manufacturer_order_detail.html"
    context_object_name = "order"

    def get_queryset(self):
        return (
            ManufacturerOrder.objects
            .for_user(self.request.user)
            .select_related("manufacturer", "brand")
            .prefetch_related(
                "items__product",
                "items__batch",
                "items__source_purchase_order_item__purchase_order",
                "payments",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["invoice_form"] = ManufacturerOrderInvoiceForm(order=self.object)
        context["outcome_form"] = ManufacturerOrderOutcomeForm()
        context["payment_form"] = ManufacturerOrderPaymentForm()
        return context


class MarkManufacturerOrderReceivedView(OwnerRequiredMixin, View):
    def post(self, request, pk):
        try:
            mark_manufacturer_order_received(actor=request.user, order_id=pk)
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("manufacturer-order-detail", pk=pk)

        messages.success(
            request,
            "Marked received. This stock is not yet in any warehouse — "
            "that happens separately.",
        )
        return redirect("manufacturer-order-detail", pk=pk)


class RecordManufacturerInvoiceView(OwnerRequiredMixin, View):
    def post(self, request, pk):
        order = (
            ManufacturerOrder.objects
            .for_user(request.user)
            .filter(pk=pk)
            .first()
        )

        if order is None:
            raise Http404

        form = ManufacturerOrderInvoiceForm(request.POST, request.FILES, order=order)

        if not form.is_valid():
            messages.error(request, "Please check the invoice details and try again.")
            return redirect("manufacturer-order-detail", pk=pk)

        try:
            order = record_manufacturer_invoice(
                actor=request.user,
                order_id=pk,
                invoice_file=form.cleaned_data["invoice_file"],
                invoice_number=form.cleaned_data["invoice_number"],
                item_prices=form.get_item_prices(),
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("manufacturer-order-detail", pk=pk)

        if order.stock_created:
            messages.success(
                request,
                "Invoice approved. This stock now needs to be allocated to "
                "a region and warehouse below.",
            )
            return redirect("inventory-list")

        messages.success(request, "Invoice updated.")
        return redirect("manufacturer-order-detail", pk=pk)


class SetManufacturerOrderOutcomeView(OwnerRequiredMixin, View):
    def post(self, request, pk):
        form = ManufacturerOrderOutcomeForm(request.POST)

        if not form.is_valid():
            messages.error(request, "Please select an outcome.")
            return redirect("manufacturer-order-detail", pk=pk)

        try:
            set_manufacturer_order_outcome(
                actor=request.user,
                order_id=pk,
                outcome=form.cleaned_data["outcome"],
                note=form.cleaned_data["note"],
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("manufacturer-order-detail", pk=pk)

        messages.success(request, "Outcome recorded.")
        return redirect("manufacturer-order-detail", pk=pk)


class RecordManufacturerPaymentView(OwnerRequiredMixin, View):
    def post(self, request, pk):
        form = ManufacturerOrderPaymentForm(request.POST)

        if not form.is_valid():
            messages.error(request, "Please check the payment details and try again.")
            return redirect("manufacturer-order-detail", pk=pk)

        try:
            record_manufacturer_payment(
                actor=request.user,
                order_id=pk,
                amount=form.cleaned_data["amount"],
                paid_at=form.cleaned_data["paid_at"],
                note=form.cleaned_data["note"],
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("manufacturer-order-detail", pk=pk)

        messages.success(request, "Payment recorded.")
        return redirect("manufacturer-order-detail", pk=pk)


class ManufacturerOrderPDFView(OwnerRequiredMixin, View):
    def get(self, request, pk):
        order = (
            ManufacturerOrder.objects
            .for_user(request.user)
            .select_related("manufacturer", "brand")
            .prefetch_related("items__product")
            .filter(pk=pk)
            .first()
        )

        if order is None:
            raise Http404

        from apps.core.models import Brand

        pdf_bytes = render_pdf(
            "manufacturers/manufacturer_order_pdf.html",
            {"order": order, "brand": Brand.objects.first()},
        )

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        disposition = "attachment" if request.GET.get("download") else "inline"
        response["Content-Disposition"] = f'{disposition}; filename="{order.po_number}.pdf"'

        return response
