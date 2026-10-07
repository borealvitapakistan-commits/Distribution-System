from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.mixins import OwnerRequiredMixin
from apps.core.models import Brand
from apps.core.utils import render_pdf
from apps.products.services import bottle_price_map

from .forms import (
    ManufacturerAdvancePaymentForm,
    ManufacturerForm,
    ManufacturerOrderForm,
    ManufacturerOrderInvoiceForm,
    ManufacturerOrderItemFormSet,
    ManufacturerOrderOutcomeForm,
    ManufacturerOrderPaymentForm,
    ManufacturerOrderReceiveForm,
    ManufacturerQuoteForm,
)
from .models import Manufacturer, ManufacturerOrder
from .services import (
    confirm_purchase_order,
    create_manufacturer,
    create_request_to_quote,
    record_manufacturer_quote,
    update_request_to_quote,
    receive_manufacturer_order,
    next_invoice_number,
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
        form = ManufacturerForm(request.POST, request.FILES)
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

        form = ManufacturerForm(request.POST, request.FILES, instance=manufacturer)
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
                "bottle_size": form.cleaned_data.get("bottle_size"),
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
    paginate_by = 25

    DATE_RANGES = [
        ("", "All dates"),
        ("7", "Last 7 days"),
        ("30", "Last 30 days"),
        ("90", "Last 90 days"),
        ("365", "Last 12 months"),
    ]

    def get_queryset(self):
        queryset = (
            ManufacturerOrder.objects
            .for_user(self.request.user)
            .select_related("manufacturer", "brand")
            .prefetch_related("items")
        )

        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(po_number__icontains=query)
                | Q(manufacturer__name__icontains=query)
                | Q(brand__name__icontains=query)
                | Q(items__product__name__icontains=query)
            ).distinct()

        status = self.request.GET.get("status", "")
        if status in ManufacturerOrder.Status.values:
            queryset = queryset.filter(status=status)

        days = self.request.GET.get("days", "")
        if days.isdigit():
            queryset = queryset.filter(
                created_at__gte=timezone.now() - timedelta(days=int(days))
            )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        context.update(
            {
                "status_choices": ManufacturerOrder.Status.choices,
                "date_ranges": self.DATE_RANGES,
                "filters": {
                    "q": self.request.GET.get("q", ""),
                    "status": self.request.GET.get("status", ""),
                    "days": self.request.GET.get("days", ""),
                },
                "filter_query": params.urlencode(),
            }
        )
        return context


def _brand_sheet_data(brand):
    return {
        "name": brand.name,
        "legal_name": brand.legal_name or brand.name,
        "address": brand.address,
        "phone": brand.phone,
        "email": brand.email,
        "color": brand.primary_color,
        "soft": brand.primary_color_soft,
        "logo": brand.logo.url if brand.logo else "",
    }


def document_sheet_data():
    """Everything the on-screen document needs to fill itself in as the
    Owner picks a manufacturer (the vendor on the document) or brand:
    {"manufacturers": {id: {...}}, "brands": {id: {...}}}."""
    return {
        "manufacturers": {
            str(manufacturer.pk): {
                "name": manufacturer.name,
                "address": manufacturer.address,
                "email": manufacturer.email,
                "phone": manufacturer.phone,
            }
            for manufacturer in Manufacturer.objects.filter(active=True)
        },
        "brands": {
            str(brand.pk): _brand_sheet_data(brand)
            for brand in Brand.objects.filter(active=True)
        },
    }


class RequestToQuoteFormMixin:
    """Shared by creating and editing a Request to Quote."""

    template_name = "manufacturers/manufacturer_order_form.html"

    def render_form(self, request, form, formset, order=None, service_error=None):
        brand = None
        brand_id = form["brand"].value()
        brand_id = getattr(brand_id, "pk", brand_id)
        if brand_id:
            brand = Brand.objects.filter(pk=brand_id).first()

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "formset": formset,
                "order": order,
                "service_error": service_error,
                "bottle_prices": bottle_price_map(),
                "sheet_data": document_sheet_data(),
                "brand": brand,
                "today": timezone.localdate(),
            },
        )


class ManufacturerOrderCreateView(OwnerRequiredMixin, RequestToQuoteFormMixin, View):
    """Step one: a new Request to Quote."""

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

        last_terms = (
            ManufacturerOrder.objects.exclude(terms="")
            .order_by("-created_at")
            .values_list("terms", flat=True)
            .first()
        )

        return self.render_form(
            request,
            ManufacturerOrderForm(
                initial={
                    "brand": Brand.objects.filter(active=True).first(),
                    "terms": last_terms or "",
                }
            ),
            ManufacturerOrderItemFormSet(**formset_kwargs),
        )

    def post(self, request):
        form = ManufacturerOrderForm(request.POST)
        formset = ManufacturerOrderItemFormSet(
            request.POST,
            instance=ManufacturerOrder(),
        )

        if not form.is_valid() or not formset.is_valid():
            return self.render_form(request, form, formset)

        try:
            order = create_request_to_quote(
                actor=request.user,
                manufacturer=form.cleaned_data["manufacturer"],
                brand=form.cleaned_data["brand"],
                terms=form.cleaned_data["terms"],
                items=manufacturer_order_item_rows(formset),
            )
        except (PermissionDenied, ValidationError) as exc:
            return self.render_form(request, form, formset, service_error=exc)

        messages.success(
            request,
            f"Request to Quote {order.po_number} created. Download it and send it "
            "to the manufacturer, then enter the prices when they come back.",
        )
        return redirect("manufacturer-order-detail", pk=order.pk)


class ManufacturerOrderEditView(OwnerRequiredMixin, RequestToQuoteFormMixin, View):
    """Changes a Request to Quote before the Manufacturer's quote is in."""

    def get_order(self, request, pk):
        order = _get_owner_order(request, pk)
        if not order.is_quote or order.quoted_at is not None:
            return None
        return order

    def _locked(self, request, pk):
        messages.info(
            request,
            "This Request to Quote can no longer be edited — the Manufacturer's "
            "quote is already entered.",
        )
        return redirect("manufacturer-order-detail", pk=pk)

    def get(self, request, pk):
        order = self.get_order(request, pk)
        if order is None:
            return self._locked(request, pk)

        initial = [
            {
                "product": item.product_id,
                "bottle_size": item.bottle_size,
                "quantity": f"{item.quantity.normalize():f}",
                "unit_price": item.requested_unit_price,
                "source_purchase_order_item": item.source_purchase_order_item_id,
            }
            for item in order.items.all()
        ]
        formset = ManufacturerOrderItemFormSet(
            instance=ManufacturerOrder(),
            initial=initial,
        )
        formset.extra = len(initial) or 1

        return self.render_form(
            request,
            ManufacturerOrderForm(
                initial={
                    "manufacturer": order.manufacturer,
                    "brand": order.brand,
                    "terms": order.terms,
                }
            ),
            formset,
            order=order,
        )

    def post(self, request, pk):
        order = self.get_order(request, pk)
        if order is None:
            return self._locked(request, pk)

        form = ManufacturerOrderForm(request.POST)
        formset = ManufacturerOrderItemFormSet(request.POST, instance=ManufacturerOrder())

        if not form.is_valid() or not formset.is_valid():
            return self.render_form(request, form, formset, order=order)

        try:
            update_request_to_quote(
                actor=request.user,
                order_id=order.pk,
                manufacturer=form.cleaned_data["manufacturer"],
                brand=form.cleaned_data["brand"],
                terms=form.cleaned_data["terms"],
                items=manufacturer_order_item_rows(formset),
            )
        except (PermissionDenied, ValidationError) as exc:
            return self.render_form(request, form, formset, order=order, service_error=exc)

        messages.success(request, f"{order.po_number} updated.")
        return redirect("manufacturer-order-detail", pk=order.pk)


class ManufacturerOrderQuoteView(OwnerRequiredMixin, View):
    """The Manufacturer's reply: upload their document and type in the
    real prices. Changed prices are highlighted yellow."""

    template_name = "manufacturers/manufacturer_order_quote.html"

    def _render(self, request, order, form):
        return render(
            request,
            self.template_name,
            {"order": order, "form": form, "brand": _order_brand(order)},
        )

    def _get(self, request, pk):
        order = _get_owner_order(request, pk)
        if not order.is_quote:
            messages.info(request, f"{order.po_number} is already a Purchase Order.")
            return order, redirect("manufacturer-order-detail", pk=order.pk)
        return order, None

    def _form(self, order, *args):
        return ManufacturerQuoteForm(
            *args,
            order=order,
            saved_prices=bottle_price_map(
                products=order.items.values_list("product_id", flat=True)
            ),
        )

    def get(self, request, pk):
        order, done = self._get(request, pk)
        return done or self._render(request, order, self._form(order))

    def post(self, request, pk):
        order, done = self._get(request, pk)
        if done:
            return done

        form = self._form(order, request.POST, request.FILES)

        if not form.is_valid():
            return self._render(request, order, form)

        try:
            record_manufacturer_quote(
                actor=request.user,
                order_id=order.pk,
                item_updates=form.item_updates(),
                quote_file=form.cleaned_data["quote_file"],
                remove_item_ids=form.removed_item_ids(),
                save_price_item_ids=form.save_price_item_ids(),
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return self._render(request, order, form)

        saved = len(form.save_price_item_ids())
        messages.success(
            request,
            "Manufacturer's quote saved."
            + (f" {saved} saved price(s) updated." if saved else "")
            + " Review the highlighted changes, then send the Purchase Order.",
        )
        return redirect("manufacturer-order-detail", pk=order.pk)


class ConfirmPurchaseOrderView(OwnerRequiredMixin, View):
    """Step two: the quote becomes the Purchase Order."""

    def post(self, request, pk):
        try:
            order = confirm_purchase_order(actor=request.user, order_id=pk)
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
            return redirect("manufacturer-order-detail", pk=pk)

        messages.success(
            request,
            f"Purchase Order {order.po_number} is ready — download it and send it "
            "to the manufacturer. Now tell us about the advance payment.",
        )
        return redirect("manufacturer-order-advance", pk=order.pk)


def order_steps(order):
    """The Request to Quote → Purchase Order → Invoice tracker at the top
    of an order. Each step is "done", "current" or "upcoming"."""
    invoiced = order.invoice_approved_at is not None

    if order.is_quote:
        states = ["current", "upcoming", "upcoming"]
    elif not invoiced:
        states = ["done", "current", "upcoming"]
    else:
        states = ["done", "done", "done"]

    quote_note = (
        "Quote received" if order.quoted_at else "Waiting for the prices from the manufacturer"
    )
    po_note = "With the manufacturer"
    if order.status == ManufacturerOrder.Status.RECEIVED or order.received_at:
        po_note = "Goods received"

    return [
        {
            "number": 1,
            "title": "Request to Quote",
            "state": states[0],
            "date": order.created_at,
            "note": quote_note if states[0] == "current" else "Completed",
        },
        {
            "number": 2,
            "title": "Purchase Order",
            "state": states[1],
            "date": order.purchase_order_date,
            "note": {"current": po_note, "done": "Completed"}.get(states[1], "Upcoming"),
        },
        {
            "number": 3,
            "title": "Invoice",
            "state": states[2],
            "date": order.invoice_approved_at,
            "note": "Completed" if invoiced else "Upcoming",
        },
    ]


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
        context["steps"] = order_steps(self.object)
        context["items"] = list(self.object.items.all())
        context["changed_count"] = sum(
            1 for item in context["items"] if item.price_highlight == "changed"
        )
        context["new_price_count"] = sum(
            1 for item in context["items"] if item.price_highlight == "new"
        )
        context["can_edit_quote"] = self.object.is_quote and self.object.quoted_at is None
        context["brand"] = _order_brand(self.object)
        return context


def _get_owner_order(request, pk):
    order = (
        ManufacturerOrder.objects
        .for_user(request.user)
        .select_related("manufacturer", "brand")
        .filter(pk=pk)
        .first()
    )

    if order is None:
        raise Http404

    return order


def _order_brand(order):
    """The brand whose logo and colour the order is shown in."""
    return order.brand or Brand.objects.filter(active=True).first() or Brand()


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
                "next_invoice_number": next_invoice_number(order),
            },
        )

    def _nothing_to_do(self, request, order):
        needs_receipt, needs_invoice = receiving_steps(order)
        if not needs_receipt and not needs_invoice:
            messages.info(request, "This order has already been received and invoiced.")
            return redirect("manufacturer-order-detail", pk=order.pk)
        if needs_receipt and order.pays_advance is None:
            messages.error(
                request,
                "Upload the payment proof first — say whether you paid an advance or not.",
            )
            return redirect("manufacturer-order-advance", pk=order.pk)
        return None

    def get(self, request, pk):
        order = _get_owner_order(request, pk)
        needs_invoice = receiving_steps(order)[1]

        return self._nothing_to_do(request, order) or self._render(
            request,
            order,
            ManufacturerOrderReceiveForm(
                initial={"amount": order.remaining_amount, "paid_at": timezone.localdate()}
            ),
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
            remaining = max(total - order.total_paid, Decimal("0.00"))
            if remaining > 0:
                form.require_full_payment(remaining)

        if form.errors or (invoice_form is not None and invoice_form.errors):
            return self._render(request, order, form, invoice_form)

        try:
            order = receive_manufacturer_order(
                actor=request.user,
                order_id=order.pk,
                item_prices=invoice_form.get_item_prices() if invoice_form else None,
                supplier_invoice_ref=(
                    invoice_form.cleaned_data["supplier_invoice_ref"] if invoice_form else ""
                ),
                to_location=invoice_form.cleaned_data["warehouse"] if invoice_form else None,
                invoice_file=invoice_form.cleaned_data["invoice_file"] if invoice_form else None,
                shipping_amount=(
                    invoice_form.charges(order)[1] if invoice_form else None
                ),
                tax_percentage=(
                    invoice_form.charges(order)[0] if invoice_form else None
                ),
                paid_remaining=bool(form.cleaned_data["amount"]),
                amount=form.cleaned_data["amount"],
                paid_at=form.cleaned_data["paid_at"],
                proof=form.cleaned_data["proof"],
                note=form.cleaned_data["note"],
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return self._render(request, order, form, invoice_form)

        if order.stock_created:
            warehouse = invoice_form.cleaned_data["warehouse"]
            messages.success(
                request,
                f"{order.po_number} received, paid in full and invoiced as "
                f"{order.invoice_number}. The stock is now in {warehouse.name}.",
            )
            return redirect("manufacturer-order-detail", pk=order.pk)

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


def document_table(items):
    """Lays order lines out like the paper Request to Quote / Purchase
    Order: one row per product, one unit-price column per bottle size
    used on the order (plus a plain "Unit price" column for lines
    without a size). Returns (columns, rows)."""
    sizes = sorted({item.bottle_size for item in items if item.bottle_size})
    columns = [
        {"size": size, "label": f"Unit price ({size} caps)"} for size in sizes
    ]
    if any(not item.bottle_size for item in items):
        columns.append({"size": None, "label": "Unit price"})

    grouped = {}
    for item in items:
        grouped.setdefault(item.product_id, []).append(item)

    rows = []
    for lines in grouped.values():
        by_size = {line.bottle_size: line for line in lines}
        quantities = {line.quantity for line in lines}
        same_quantity = len(quantities) == 1
        priced = [line for line in lines if line.unit_price is not None]

        rows.append(
            {
                "product": lines[0].product,
                "quantity": quantities.pop() if same_quantity else None,
                "cells": [
                    {"item": by_size.get(column["size"]), "show_quantity": not same_quantity}
                    for column in columns
                ],
                "total": (
                    sum((line.line_total for line in priced), Decimal("0.00"))
                    if priced else None
                ),
                "highlight": (
                    "changed" if any(line.price_highlight == "changed" for line in lines)
                    else "new" if any(line.price_highlight == "new" for line in lines)
                    else ""
                ),
            }
        )

    return columns, rows


class ManufacturerOrderPDFView(OwnerRequiredMixin, View):
    """The Request to Quote or Purchase Order, themed in the order's brand
    logo and colour. ?copy=team is the internal copy with changed prices
    highlighted yellow; the default is the clean copy for the Manufacturer."""

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

        team_copy = request.GET.get("copy") == "team"
        brand = _order_brand(order)
        columns, rows = document_table(list(order.items.all()))

        pdf_bytes = render_pdf(
            "manufacturers/manufacturer_order_pdf.html",
            {
                "order": order,
                "brand": brand,
                "columns": columns,
                "rows": rows,
                "highlight_changes": team_copy,
            },
        )

        kind = "RTQ" if order.is_quote else "PO"
        suffix = "-team" if team_copy else ""
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        disposition = "attachment" if request.GET.get("download") else "inline"
        response["Content-Disposition"] = (
            f'{disposition}; filename="{kind}-{order.po_number}{suffix}.pdf"'
        )

        return response
