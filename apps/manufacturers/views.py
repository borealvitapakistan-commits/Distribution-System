from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.mixins import OwnerRequiredMixin
from apps.core.utils import render_pdf

from .forms import (
    ManufacturerAdvancePaymentForm,
    ManufacturerForm,
    ManufacturerOrderForm,
    ManufacturerOrderInvoiceForm,
    ManufacturerOrderItemFormSet,
    ManufacturerOrderOutcomeForm,
    ManufacturerOrderPaymentForm,
    ManufacturerOrderReceiveForm,
)
from .models import Manufacturer, ManufacturerOrder
from .services import (
    create_manufacturer,
    create_manufacturer_order,
    receive_manufacturer_order,
    receiving_steps,
    record_advance_decision,
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

        messages.success(request, f"Order {order.po_number} placed.")
        return redirect("manufacturer-order-advance", pk=order.pk)


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
        context["outcome_form"] = ManufacturerOrderOutcomeForm()
        context["payment_form"] = ManufacturerOrderPaymentForm()
        return context


def _get_owner_order(request, pk):
    order = (
        ManufacturerOrder.objects
        .for_user(request.user)
        .select_related("manufacturer")
        .filter(pk=pk)
        .first()
    )

    if order is None:
        raise Http404

    return order


class ManufacturerOrderAdvanceView(OwnerRequiredMixin, View):
    """Step two of placing an order: "Are you paying in advance?"."""

    template_name = "manufacturers/manufacturer_order_advance.html"

    def _render(self, request, order, form):
        fill_amounts = []
        percentage = order.manufacturer.upfront_payment_percentage

        if 0 < percentage < 100:
            fill_amounts.append(
                {
                    "label": f"Usual {percentage.normalize():f}% ({order.required_upfront_amount})",
                    "value": order.required_upfront_amount,
                }
            )

        fill_amounts.append({"label": "Full amount (100%)", "value": order.grand_total})

        return render(
            request,
            self.template_name,
            {"order": order, "form": form, "fill_amounts": fill_amounts},
        )

    def _already_answered(self, request, order):
        if order.status != ManufacturerOrder.Status.SENT or order.pays_advance is not None:
            messages.info(request, "The advance payment for this order is already answered.")
            return redirect("manufacturer-order-detail", pk=order.pk)
        return None

    def get(self, request, pk):
        order = _get_owner_order(request, pk)
        return self._already_answered(request, order) or self._render(
            request,
            order,
            ManufacturerAdvancePaymentForm(
                initial={"amount": order.required_upfront_amount or None}
            ),
        )

    def post(self, request, pk):
        order = _get_owner_order(request, pk)
        answered = self._already_answered(request, order)
        if answered:
            return answered

        form = ManufacturerAdvancePaymentForm(request.POST, request.FILES)

        if not form.is_valid():
            return self._render(request, order, form)

        try:
            record_advance_decision(
                actor=request.user,
                order_id=order.pk,
                pays_advance=form.cleaned_data["answer"],
                amount=form.cleaned_data["amount"],
                paid_at=form.cleaned_data["paid_at"],
                proof=form.cleaned_data["proof"],
                note=form.cleaned_data["note"],
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return self._render(request, order, form)

        messages.success(
            request,
            "Advance payment recorded." if form.cleaned_data["answer"]
            else "No advance payment — the full amount is due when the order arrives.",
        )
        return redirect("manufacturer-order-detail", pk=order.pk)


class ManufacturerOrderReceiveView(OwnerRequiredMixin, View):
    """Step three: the goods arrived. One page to confirm it, enter the
    Manufacturer's invoice (the actual quantity, price and expiry per
    line — this is what puts the stock into Inventory) and settle
    whatever is left to pay on the invoiced total."""

    template_name = "manufacturers/manufacturer_order_receive.html"

    def _render(self, request, order, form, invoice_form):
        needs_receipt, needs_invoice = receiving_steps(order)
        return render(
            request,
            self.template_name,
            {
                "order": order,
                "form": form,
                "invoice_form": invoice_form,
                "needs_receipt": needs_receipt,
                "needs_invoice": needs_invoice,
                "fill_amounts": [
                    {"label": "Remaining amount", "value": order.remaining_amount}
                ],
            },
        )

    def _nothing_to_do(self, request, order):
        if not any(receiving_steps(order)):
            messages.info(request, "This order has already been received and invoiced.")
            return redirect("manufacturer-order-detail", pk=order.pk)
        return None

    def get(self, request, pk):
        order = _get_owner_order(request, pk)
        needs_invoice = receiving_steps(order)[1]

        return self._nothing_to_do(request, order) or self._render(
            request,
            order,
            ManufacturerOrderReceiveForm(initial={"amount": order.remaining_amount}),
            ManufacturerOrderInvoiceForm(order=order) if needs_invoice else None,
        )

    def post(self, request, pk):
        order = _get_owner_order(request, pk)
        done = self._nothing_to_do(request, order)
        if done:
            return done

        needs_invoice = receiving_steps(order)[1]
        form = ManufacturerOrderReceiveForm(request.POST, request.FILES)
        invoice_form = (
            ManufacturerOrderInvoiceForm(request.POST, request.FILES, order=order)
            if needs_invoice
            else None
        )

        forms_valid = form.is_valid()
        if invoice_form is not None:
            forms_valid = invoice_form.is_valid() and forms_valid

        if forms_valid:
            total = (
                invoice_form.projected_grand_total(order)
                if invoice_form is not None
                else order.grand_total
            )
            if total > order.total_paid and form.cleaned_data["answer"] is None:
                form.add_error("answer", "Please answer — is the remaining amount paid?")

        if form.errors or (invoice_form is not None and invoice_form.errors):
            return self._render(request, order, form, invoice_form)

        try:
            order = receive_manufacturer_order(
                actor=request.user,
                order_id=order.pk,
                item_prices=invoice_form.get_item_prices() if invoice_form else None,
                paid_remaining=bool(form.cleaned_data["answer"]),
                amount=form.cleaned_data["amount"],
                paid_at=form.cleaned_data["paid_at"],
                proof=form.cleaned_data["proof"],
                note=form.cleaned_data["note"],
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return self._render(request, order, form, invoice_form)

        if order.stock_created:
            messages.success(
                request,
                f"{order.po_number} received and invoice approved. This stock "
                "now needs to be allocated to a region and warehouse below.",
            )
            return redirect("inventory-list")

        messages.success(request, f"{order.po_number} marked received.")
        return redirect("manufacturer-order-detail", pk=order.pk)


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
        form = ManufacturerOrderPaymentForm(request.POST, request.FILES)

        if not form.is_valid():
            messages.error(
                request,
                "Please enter the amount, date paid and payment proof, then try again.",
            )
            return redirect("manufacturer-order-detail", pk=pk)

        try:
            record_manufacturer_payment(
                actor=request.user,
                order_id=pk,
                amount=form.cleaned_data["amount"],
                paid_at=form.cleaned_data["paid_at"],
                proof=form.cleaned_data["proof"],
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
