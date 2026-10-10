from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import OuterRef, Subquery
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from apps.accounts.mixins import (
    DistributorRequiredMixin,
    OwnerRequiredMixin,
)
from apps.agreements.services import agreement_in_force
from apps.core.models import Brand
from apps.core.utils import render_pdf

from .forms import (
    CounterOfferForm,
    DeclinePurchaseOrderForm,
    DistributorAdvancePaymentForm,
    IssueInvoiceForm,
    LineStatusForm,
    OwnerCommentForm,
    OwnerQuoteForm,
    PricingForm,
    PurchaseOrderChangeForm,
    PurchaseOrderItemFormSet,
    PurchaseOrderReceiveForm,
    RecordPaymentForm,
    RejectPaymentForm,
    RequestToQuoteForm,
    ShipPurchaseOrderItemForm,
)
from .models import (
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderPayment,
    PurchaseOrderRevision,
)
from .services import (
    accept_quote,
    add_owner_comment,
    awaiting_distributor_reply,
    awaiting_owner_reply,
    confirm_payment,
    create_request_to_quote,
    decline_purchase_order,
    issue_invoice,
    mark_purchase_order_viewed,
    mark_seen_by_distributor,
    next_invoice_number,
    order_steps,
    receive_purchase_order,
    record_advance_decision,
    record_counter_offer,
    record_payment,
    reject_payment,
    revision_timeline,
    send_quote,
    ship_purchase_order_item,
    update_line_discounts,
    update_line_status,
    update_pricing,
    update_purchase_order,
    update_request_to_quote,
)


def _brand():
    """The brand whose logo and colour the documents are shown in."""
    return Brand.objects.filter(active=True).first() or Brand.objects.first() or Brand()


def _error_text(exc):
    return " ".join(getattr(exc, "messages", [str(exc)]))


def with_latest_stage(queryset):
    """Adds latest_stage — the last step said on each order — so a list
    can show whose turn it is without a query per row."""
    latest = (
        PurchaseOrderRevision.objects
        .filter(purchase_order=OuterRef("pk"))
        .order_by("-number")
        .values("stage")[:1]
    )
    return queryset.annotate(latest_stage=Subquery(latest))


def purchase_order_item_rows(formset):
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
                "quantity_requested": form.cleaned_data["quantity_requested"],
                "unit_price": form.cleaned_data.get("requested_unit_price"),
            }
        )

    return rows


def _get_distributor_order(request, pk):
    purchase_order = (
        PurchaseOrder.objects
        .for_user(request.user)
        .select_related("distributor_profile", "agreement")
        .filter(pk=pk)
        .first()
    )

    if purchase_order is None:
        raise Http404

    return purchase_order


def _get_owner_order(request, pk):
    purchase_order = (
        PurchaseOrder.objects
        .for_user(request.user)
        .select_related("distributor_profile", "agreement")
        .filter(pk=pk)
        .first()
    )

    if purchase_order is None:
        raise Http404

    return purchase_order


def _latest_lines(purchase_order):
    """The last prices on the table, per live order line."""
    latest = purchase_order.revisions.order_by("-number").first()
    if latest is None:
        return {}
    return {
        str(line.item_id): line
        for line in latest.lines.all()
        if line.item_id and not line.removed
    }


def _order_context(purchase_order):
    """What both panels' order pages show: the four-stage tracker, the
    lines with their highlights and — while still negotiating, or once
    the order is complete — the conversation."""
    items = list(purchase_order.items.all())
    show_conversation = (
        purchase_order.is_quote
        or purchase_order.declined_as_quote
        or purchase_order.is_invoiced
    )
    return {
        "steps": order_steps(purchase_order),
        "items": items,
        "changed_count": sum(1 for item in items if item.price_highlight == "changed"),
        "new_price_count": sum(1 for item in items if item.price_highlight == "new"),
        "awaiting_owner": awaiting_owner_reply(purchase_order),
        "awaiting_distributor": awaiting_distributor_reply(purchase_order),
        "show_conversation": show_conversation,
        "timeline": revision_timeline(purchase_order) if show_conversation else [],
        "doc_brand": _brand(),
    }


class RequestToQuoteFormMixin:
    """Shared by creating and editing a Request to Quote."""

    template_name = "requests/purchase_order_form.html"

    def _agreement(self, request, purchase_order=None):
        if purchase_order is not None:
            return purchase_order.agreement
        profile = getattr(request.user, "distributor_profile", None)
        return agreement_in_force(profile) if profile else None

    def _render(self, request, formset, form, agreement, purchase_order=None, service_error=None):
        return render(
            request,
            self.template_name,
            {
                "formset": formset,
                "form": form,
                "agreement": agreement,
                "purchase_order": purchase_order,
                "service_error": service_error,
                "doc_brand": _brand(),
                "distributor_profile": getattr(request.user, "distributor_profile", None),
                "today": timezone.localdate(),
            },
        )


class PurchaseOrderCreateView(DistributorRequiredMixin, RequestToQuoteFormMixin, View):
    """Step one: the Distributor's Request to Quote."""

    def get(self, request):
        agreement = self._agreement(request)
        formset = PurchaseOrderItemFormSet(
            instance=PurchaseOrder(),
            form_kwargs={"agreement": agreement},
        )
        return self._render(request, formset, RequestToQuoteForm(), agreement)

    def post(self, request):
        agreement = self._agreement(request)
        form = RequestToQuoteForm(request.POST)
        formset = PurchaseOrderItemFormSet(
            request.POST,
            instance=PurchaseOrder(),
            form_kwargs={"agreement": agreement},
        )

        if not form.is_valid() or not formset.is_valid():
            return self._render(request, formset, form, agreement)

        try:
            purchase_order = create_request_to_quote(
                actor=request.user,
                items=purchase_order_item_rows(formset),
                message=form.cleaned_data["message"],
            )
        except (PermissionDenied, ValidationError) as exc:
            return self._render(request, formset, form, agreement, service_error=exc)

        messages.success(
            request,
            f"Request to Quote {purchase_order.po_number} sent to the Owner. "
            "You'll be notified here when their quote comes back.",
        )
        return redirect("distributor-purchase-order-detail", pk=purchase_order.pk)


class PurchaseOrderEditView(DistributorRequiredMixin, RequestToQuoteFormMixin, View):
    """Changes a Request to Quote before the Owner has quoted."""

    def get_order(self, request, pk):
        purchase_order = _get_distributor_order(request, pk)
        if not purchase_order.is_quote or purchase_order.quoted_at is not None:
            return purchase_order, None
        return purchase_order, True

    def _locked(self, request, purchase_order):
        messages.info(
            request,
            "This Request to Quote can no longer be edited — the Owner has already quoted.",
        )
        return redirect("distributor-purchase-order-detail", pk=purchase_order.pk)

    def get(self, request, pk):
        purchase_order, editable = self.get_order(request, pk)
        if not editable:
            return self._locked(request, purchase_order)

        agreement = self._agreement(request, purchase_order)
        initial = [
            {
                "product": item.product_id,
                "quantity_requested": f"{item.quantity_requested.normalize():f}",
                "requested_unit_price": item.requested_unit_price,
            }
            for item in purchase_order.items.all()
        ]
        formset = PurchaseOrderItemFormSet(
            instance=PurchaseOrder(),
            initial=initial,
            form_kwargs={"agreement": agreement},
        )
        formset.extra = len(initial) or 1

        message = (
            purchase_order.revisions.filter(stage=PurchaseOrderRevision.Stage.REQUEST)
            .values_list("message", flat=True)
            .first()
        ) or ""

        return self._render(
            request,
            formset,
            RequestToQuoteForm(initial={"message": message}),
            agreement,
            purchase_order=purchase_order,
        )

    def post(self, request, pk):
        purchase_order, editable = self.get_order(request, pk)
        if not editable:
            return self._locked(request, purchase_order)

        agreement = self._agreement(request, purchase_order)
        form = RequestToQuoteForm(request.POST)
        formset = PurchaseOrderItemFormSet(
            request.POST,
            instance=PurchaseOrder(),
            form_kwargs={"agreement": agreement},
        )

        if not form.is_valid() or not formset.is_valid():
            return self._render(request, formset, form, agreement, purchase_order)

        try:
            update_request_to_quote(
                actor=request.user,
                purchase_order_id=purchase_order.pk,
                items=purchase_order_item_rows(formset),
                message=form.cleaned_data["message"],
            )
        except (PermissionDenied, ValidationError) as exc:
            return self._render(
                request, formset, form, agreement, purchase_order, service_error=exc
            )

        messages.success(request, f"{purchase_order.po_number} updated.")
        return redirect("distributor-purchase-order-detail", pk=purchase_order.pk)


class DistributorOrdersHubView(DistributorRequiredMixin, TemplateView):
    template_name = "requests/distributor_orders_hub.html"


class DistributorPurchaseOrderListView(DistributorRequiredMixin, ListView):
    template_name = "requests/distributor_purchase_order_list.html"
    context_object_name = "purchase_orders"

    def get_queryset(self):
        return with_latest_stage(
            PurchaseOrder.objects
            .for_user(self.request.user)
            .prefetch_related("items__product")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["updated_ids"] = set(
            PurchaseOrder.objects.for_user(self.request.user)
            .with_updates_for_distributor()
            .values_list("pk", flat=True)
        )
        return context


class DistributorPurchaseOrderDetailView(DistributorRequiredMixin, DetailView):
    template_name = "requests/distributor_purchase_order_detail.html"
    context_object_name = "purchase_order"

    def get_queryset(self):
        return (
            PurchaseOrder.objects
            .for_user(self.request.user)
            .select_related("agreement", "distributor_profile")
            .prefetch_related("items__product", "payments")
        )

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        latest_owner_step = (
            self.object.revisions.filter(by_owner=True)
            .order_by("-number")
            .values_list("number", flat=True)
            .first()
        ) or 0
        self.has_update = latest_owner_step > self.object.distributor_seen_revision
        mark_seen_by_distributor(actor=request.user, purchase_order=self.object)
        return self.render_to_response(self.get_context_data(object=self.object))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_order_context(self.object))
        context["payment_form"] = RecordPaymentForm()
        context["has_update"] = getattr(self, "has_update", False)
        context["can_edit_request"] = (
            self.object.is_quote and self.object.quoted_at is None
        )
        return context


class OwnerPurchaseOrdersHubView(OwnerRequiredMixin, TemplateView):
    template_name = "requests/owner_purchase_orders_hub.html"


class OwnerPurchaseOrderListView(OwnerRequiredMixin, ListView):
    template_name = "requests/owner_purchase_order_list.html"
    context_object_name = "purchase_orders"

    def get_queryset(self):
        return with_latest_stage(
            PurchaseOrder.objects
            .for_user(self.request.user)
            .select_related("distributor_profile")
            .prefetch_related("items")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["updated_ids"] = set(
            PurchaseOrder.objects.with_updates_for_owner().values_list("pk", flat=True)
        )
        return context


class OwnerPurchaseOrderDetailView(OwnerRequiredMixin, DetailView):
    template_name = "requests/owner_purchase_order_detail.html"
    context_object_name = "purchase_order"

    def get_queryset(self):
        return (
            PurchaseOrder.objects
            .for_user(self.request.user)
            .select_related("distributor_profile", "agreement")
            .prefetch_related(
                "items__product",
                "items__manufacturer_order_items__order",
                "payments",
            )
        )

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.was_new = self.object.owner_viewed_at is None
        latest_distributor_step = (
            self.object.revisions.filter(by_owner=False)
            .order_by("-number")
            .values_list("number", flat=True)
            .first()
        ) or 0
        self.has_update = (
            not self.was_new and latest_distributor_step > self.object.owner_seen_revision
        )
        mark_purchase_order_viewed(actor=request.user, purchase_order=self.object)
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_order_context(self.object))
        context["items_with_ship_forms"] = [
            (item, ShipPurchaseOrderItemForm(product=item.product))
            for item in self.object.items.all()
        ]
        context["items_with_status_forms"] = [
            (
                item,
                LineStatusForm(
                    initial={"note": item.owner_note, "unavailable": item.unavailable},
                    prefix=f"line-{item.pk}",
                ),
            )
            for item in self.object.items.all()
        ]
        context["any_repriceable"] = (
            self.object.is_open
            and not self.object.is_quote
            and not self.object.is_invoiced
            and any(item.can_reprice for item in self.object.items.all())
        )
        context["decline_form"] = DeclinePurchaseOrderForm()
        context["comment_form"] = OwnerCommentForm(
            initial={"comment": self.object.owner_comment}
        )
        context["pricing_form"] = PricingForm(instance=self.object)
        context["reject_payment_form"] = RejectPaymentForm()
        context["was_new"] = getattr(self, "was_new", False)
        context["has_update"] = getattr(self, "has_update", False)
        if self.object.is_quote:
            # While it's a Request to Quote the document on the page is the
            # Owner's quote itself — prices editable, sent back from here.
            context["quote_form"] = OwnerQuoteForm(
                purchase_order=self.object,
                initial_lines=_latest_lines(self.object),
            )
            context["quote_submit_label"] = (
                "Send Revised Quote to Distributor" if self.object.quoted_at
                else "Send Quote to Distributor"
            )
        return context


class OrderLinesFormView(View):
    """A page with one price and quantity per line plus a message — the
    Owner's quote, the Distributor's counter-offer, or the Owner changing
    a placed Purchase Order."""

    template_name = "requests/purchase_order_lines_form.html"
    form_class = None
    page_title = ""
    intro = ""
    submit_label = ""
    panel = ""  # "owner" or "distributor"

    def get_order(self, request, pk):
        raise NotImplementedError

    def allowed(self, purchase_order):
        raise NotImplementedError

    def not_allowed(self, request, purchase_order):
        raise NotImplementedError

    def initial_lines(self, purchase_order):
        return {}

    def save(self, request, purchase_order, form):
        raise NotImplementedError

    def detail_url(self, purchase_order):
        name = (
            "owner-purchase-order-detail" if self.panel == "owner"
            else "distributor-purchase-order-detail"
        )
        return reverse(name, args=[purchase_order.pk])

    def _render(self, request, purchase_order, form):
        return render(
            request,
            self.template_name,
            {
                "purchase_order": purchase_order,
                "form": form,
                "page_title": self.page_title,
                "intro": self.intro,
                "submit_label": self.submit_label,
                "panel": self.panel,
                "detail_url": self.detail_url(purchase_order),
                "doc_brand": _brand(),
            },
        )

    def _form(self, purchase_order, *args):
        return self.form_class(
            *args,
            purchase_order=purchase_order,
            initial_lines=self.initial_lines(purchase_order),
        )

    def get(self, request, pk):
        purchase_order = self.get_order(request, pk)
        if not self.allowed(purchase_order):
            return self.not_allowed(request, purchase_order)
        return self._render(request, purchase_order, self._form(purchase_order))

    def post(self, request, pk):
        purchase_order = self.get_order(request, pk)
        if not self.allowed(purchase_order):
            return self.not_allowed(request, purchase_order)

        form = self._form(purchase_order, request.POST, request.FILES)
        if not form.is_valid():
            return self._render(request, purchase_order, form)

        try:
            self.save(request, purchase_order, form)
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return self._render(request, purchase_order, form)

        return redirect(self.success_url(purchase_order))

    def success_url(self, purchase_order):
        return self.detail_url(purchase_order)


class OwnerQuoteView(OwnerRequiredMixin, OrderLinesFormView):
    """The Owner answers a Request to Quote — or the Distributor's
    counter-offer — from the Owner's panel. Changed prices are
    highlighted against what the Distributor asked for."""

    template_name = "requests/owner_quote.html"
    form_class = OwnerQuoteForm
    page_title = "Quote"
    submit_label = "Send Quote to Distributor"
    panel = "owner"

    def get_order(self, request, pk):
        return _get_owner_order(request, pk)

    def initial_lines(self, purchase_order):
        # Start from the last prices said — the Distributor's request or
        # their counter-offer — so agreeing to them needs no retyping.
        return _latest_lines(purchase_order)

    def allowed(self, purchase_order):
        return purchase_order.is_quote

    def not_allowed(self, request, purchase_order):
        messages.info(
            request,
            f"{purchase_order.po_number} is no longer a Request to Quote.",
        )
        return redirect("owner-purchase-order-detail", pk=purchase_order.pk)

    def save(self, request, purchase_order, form):
        send_quote(
            actor=request.user,
            purchase_order_id=purchase_order.pk,
            item_updates=form.item_updates(),
            remove_item_ids=form.removed_item_ids(),
            message=form.cleaned_data["message"],
            attachment=form.cleaned_data.get("attachment"),
        )
        messages.success(
            request,
            f"Quote sent to {purchase_order.distributor_profile.name}. They'll be "
            "notified to accept it or send a counter-offer.",
        )


class OwnerPurchaseOrderChangeView(OwnerRequiredMixin, OrderLinesFormView):
    """The Owner changes a placed Purchase Order, saved as a new version."""

    form_class = PurchaseOrderChangeForm
    page_title = "Change Purchase Order"
    intro = (
        "Change prices or quantities, or drop a line. The current Purchase Order "
        "is kept as it was, and this becomes its next version — the Distributor "
        "is notified."
    )
    submit_label = "Save New Version"
    panel = "owner"

    def get_order(self, request, pk):
        return _get_owner_order(request, pk)

    def allowed(self, purchase_order):
        return purchase_order.can_change

    def not_allowed(self, request, purchase_order):
        messages.info(
            request,
            "Only a Purchase Order with nothing shipped and no invoice yet can be changed.",
        )
        return redirect("owner-purchase-order-detail", pk=purchase_order.pk)

    def save(self, request, purchase_order, form):
        update_purchase_order(
            actor=request.user,
            purchase_order_id=purchase_order.pk,
            item_updates=form.item_updates(),
            remove_item_ids=form.removed_item_ids(),
            message=form.cleaned_data["message"],
        )
        messages.success(
            request,
            f"Purchase Order {purchase_order.po_number} updated — the Distributor "
            "has been notified of the new version.",
        )


class DistributorCounterOfferView(DistributorRequiredMixin, OrderLinesFormView):
    """The Distributor answers the Owner's quote with the prices they want."""

    form_class = CounterOfferForm
    page_title = "Counter-offer"
    intro = (
        "Starting from the Owner's latest prices — change the ones you want to "
        "negotiate. The Owner is notified and answers with a new quote."
    )
    submit_label = "Send Counter-offer"
    panel = "distributor"

    def get_order(self, request, pk):
        return _get_distributor_order(request, pk)

    def success_url(self, purchase_order):
        return f"{super().success_url(purchase_order)}#conversation"

    def allowed(self, purchase_order):
        return awaiting_distributor_reply(purchase_order)

    def not_allowed(self, request, purchase_order):
        messages.info(
            request,
            "A counter-offer answers the Owner's quote — "
            + (
                "wait for the Owner to reply first."
                if purchase_order.is_quote
                else "this is already a Purchase Order."
            ),
        )
        return redirect("distributor-purchase-order-detail", pk=purchase_order.pk)

    def save(self, request, purchase_order, form):
        revision = record_counter_offer(
            actor=request.user,
            purchase_order_id=purchase_order.pk,
            item_updates=form.item_updates(),
            message=form.cleaned_data["message"],
            attachment=form.cleaned_data.get("attachment"),
        )
        messages.success(
            request,
            f"Counter-offer sent (step {revision.number}). The Owner has been notified.",
        )


class AcceptQuoteView(DistributorRequiredMixin, View):
    """Step three: the Distributor accepts the Owner's quote and it becomes
    their Purchase Order."""

    def post(self, request, pk):
        purchase_order = _get_distributor_order(request, pk)

        try:
            accept_quote(
                actor=request.user,
                purchase_order_id=purchase_order.pk,
                message=request.POST.get("message", ""),
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, _error_text(exc))
            return redirect("distributor-purchase-order-detail", pk=pk)

        messages.success(
            request,
            f"Purchase Order {purchase_order.po_number} placed with the Owner. "
            "Now tell us about the advance payment.",
        )
        return redirect("distributor-purchase-order-advance", pk=pk)


class IssueInvoiceView(OwnerRequiredMixin, View):
    """Step four: the Owner invoices the Distributor for what shipped."""

    template_name = "requests/purchase_order_invoice.html"

    def _render(self, request, purchase_order, form):
        return render(
            request,
            self.template_name,
            {
                "purchase_order": purchase_order,
                "form": form,
                "items": list(purchase_order.items.select_related("product")),
                "next_invoice_number": next_invoice_number(),
                "doc_brand": _brand(),
            },
        )

    def _not_ready(self, request, purchase_order):
        if purchase_order.can_issue_invoice:
            return None
        messages.info(
            request,
            f"Already invoiced as {purchase_order.invoice_number}." if purchase_order.is_invoiced
            else "The invoice goes out once everything has shipped.",
        )
        return redirect("owner-purchase-order-detail", pk=purchase_order.pk)

    def get(self, request, pk):
        purchase_order = _get_owner_order(request, pk)
        return self._not_ready(request, purchase_order) or self._render(
            request,
            purchase_order,
            IssueInvoiceForm(
                initial={
                    "tax_percentage": purchase_order.tax_percentage,
                    "shipping_amount": purchase_order.shipping_amount,
                }
            ),
        )

    def post(self, request, pk):
        purchase_order = _get_owner_order(request, pk)
        not_ready = self._not_ready(request, purchase_order)
        if not_ready:
            return not_ready

        form = IssueInvoiceForm(request.POST)
        if not form.is_valid():
            return self._render(request, purchase_order, form)

        try:
            purchase_order = issue_invoice(
                actor=request.user,
                purchase_order_id=purchase_order.pk,
                tax_percentage=form.cleaned_data["tax_percentage"],
                shipping_amount=form.cleaned_data["shipping_amount"],
                message=form.cleaned_data["message"],
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return self._render(request, purchase_order, form)

        messages.success(
            request,
            f"Invoice {purchase_order.invoice_number} issued — the Distributor has been notified.",
        )
        return redirect("owner-purchase-order-detail", pk=purchase_order.pk)


class PurchaseOrderConversationView(LoginRequiredMixin, View):
    """The whole conversation on one order, from the Request to Quote to
    the invoice — for either side to look back on."""

    template_name = "requests/purchase_order_conversation.html"

    def get(self, request, pk):
        purchase_order = _get_owner_order(request, pk)
        return render(
            request,
            self.template_name,
            {
                "purchase_order": purchase_order,
                "timeline": revision_timeline(purchase_order),
                "steps": order_steps(purchase_order),
                "panel": "owner" if request.user.is_owner else "distributor",
            },
        )


class UpdatePricingView(OwnerRequiredMixin, View):
    def post(self, request, pk):
        form = PricingForm(request.POST)

        if not form.is_valid():
            messages.error(request, "Please provide valid tax and shipping values.")
            return redirect("owner-purchase-order-detail", pk=pk)

        try:
            update_pricing(
                actor=request.user,
                purchase_order_id=pk,
                tax_percentage=form.cleaned_data["tax_percentage"],
                shipping_amount=form.cleaned_data["shipping_amount"],
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("owner-purchase-order-detail", pk=pk)

        messages.success(request, "Tax and shipping updated.")
        return redirect("owner-purchase-order-detail", pk=pk)


class UpdateLineDiscountsView(OwnerRequiredMixin, View):
    """Saves the Owner's per-line discount %s (inputs named
    discount_<item id>) — the page recomputes prices live as they type."""

    def post(self, request, pk):
        discounts = {
            key.removeprefix("discount_"): value
            for key, value in request.POST.items()
            if key.startswith("discount_")
        }

        try:
            update_line_discounts(
                actor=request.user,
                purchase_order_id=pk,
                discounts=discounts,
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
            return redirect("owner-purchase-order-detail", pk=pk)

        messages.success(request, "Prices updated.")
        return redirect("owner-purchase-order-detail", pk=pk)


class UpdateLineStatusView(OwnerRequiredMixin, View):
    def post(self, request, pk, item_id):
        form = LineStatusForm(request.POST, prefix=f"line-{item_id}")

        if not form.is_valid():
            messages.error(request, "Could not save the comment.")
            return redirect("owner-purchase-order-detail", pk=pk)

        try:
            item = update_line_status(
                actor=request.user,
                item_id=item_id,
                note=form.cleaned_data["note"],
                unavailable=form.cleaned_data["unavailable"],
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
            return redirect("owner-purchase-order-detail", pk=pk)

        messages.success(
            request,
            f"{item.product.name} closed as unavailable." if item.unavailable
            else f"Comment on {item.product.name} saved.",
        )
        return redirect("owner-purchase-order-detail", pk=pk)


class ShipPurchaseOrderItemView(OwnerRequiredMixin, View):
    def post(self, request, pk, item_id):
        item = PurchaseOrderItem.objects.filter(pk=item_id).first()
        product = item.product if item is not None else None

        form = ShipPurchaseOrderItemForm(request.POST, product=product)

        if not form.is_valid():
            messages.error(request, "Please provide valid quantities.")
            return redirect("owner-purchase-order-detail", pk=pk)

        try:
            ship_purchase_order_item(
                actor=request.user,
                item_id=item_id,
                allocations=form.get_allocations(),
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("owner-purchase-order-detail", pk=pk)

        messages.success(request, "Stock shipped successfully.")
        return redirect("owner-purchase-order-detail", pk=pk)


class DistributorPurchaseOrderAdvanceView(DistributorRequiredMixin, View):
    """Step two of placing an order: "Are you paying in advance?"."""

    template_name = "requests/purchase_order_advance.html"

    def _render(self, request, purchase_order, form):
        fill_amounts = []
        percentage = purchase_order.distributor_profile.upfront_payment_percentage

        if 0 < percentage < 100:
            fill_amounts.append(
                {
                    "label": f"Required {percentage.normalize():f}% ({purchase_order.required_upfront_amount})",
                    "value": purchase_order.required_upfront_amount,
                }
            )

        fill_amounts.append({"label": "Full amount (100%)", "value": purchase_order.grand_total})

        return render(
            request,
            self.template_name,
            {"purchase_order": purchase_order, "form": form, "fill_amounts": fill_amounts},
        )

    def _already_answered(self, request, purchase_order):
        if (
            purchase_order.status != PurchaseOrder.Status.PENDING
            or purchase_order.pays_advance is not None
        ):
            messages.info(request, "The advance payment for this order is already answered.")
            return redirect("distributor-purchase-order-detail", pk=purchase_order.pk)
        return None

    def get(self, request, pk):
        purchase_order = _get_distributor_order(request, pk)
        return self._already_answered(request, purchase_order) or self._render(
            request,
            purchase_order,
            DistributorAdvancePaymentForm(
                initial={"amount": purchase_order.required_upfront_amount or None}
            ),
        )

    def post(self, request, pk):
        purchase_order = _get_distributor_order(request, pk)
        answered = self._already_answered(request, purchase_order)
        if answered:
            return answered

        form = DistributorAdvancePaymentForm(request.POST, request.FILES)

        if not form.is_valid():
            return self._render(request, purchase_order, form)

        try:
            record_advance_decision(
                actor=request.user,
                purchase_order_id=purchase_order.pk,
                pays_advance=form.cleaned_data["answer"],
                amount=form.cleaned_data["amount"],
                paid_at=form.cleaned_data["paid_at"],
                proof=form.cleaned_data["proof"],
                note=form.cleaned_data["note"],
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return self._render(request, purchase_order, form)

        messages.success(
            request,
            "Advance payment recorded — the Owner will confirm it." if form.cleaned_data["answer"]
            else "No advance payment — the full amount is due when the order arrives.",
        )
        return redirect("distributor-purchase-order-detail", pk=purchase_order.pk)


class DistributorPurchaseOrderReceiveView(DistributorRequiredMixin, View):
    """Step three: the goods arrived — confirm what came on each line
    (this puts it into your stock) and settle whatever is left to pay."""

    template_name = "requests/purchase_order_receive.html"

    def _render(self, request, purchase_order, form):
        return render(
            request,
            self.template_name,
            {
                "purchase_order": purchase_order,
                "form": form,
                "fill_amounts": [
                    {"label": "Remaining amount", "value": purchase_order.remaining_amount}
                ],
            },
        )

    def _nothing_to_receive(self, request, purchase_order):
        if not purchase_order.awaiting_receipt:
            messages.info(request, "There's nothing shipped on this order waiting to be received.")
            return redirect("distributor-purchase-order-detail", pk=purchase_order.pk)
        return None

    def get(self, request, pk):
        purchase_order = _get_distributor_order(request, pk)
        return self._nothing_to_receive(request, purchase_order) or self._render(
            request,
            purchase_order,
            PurchaseOrderReceiveForm(
                purchase_order=purchase_order,
                initial={"amount": purchase_order.remaining_amount},
            ),
        )

    def post(self, request, pk):
        purchase_order = _get_distributor_order(request, pk)
        nothing = self._nothing_to_receive(request, purchase_order)
        if nothing:
            return nothing

        form = PurchaseOrderReceiveForm(
            request.POST, request.FILES, purchase_order=purchase_order
        )

        if form.is_valid():
            if not form.quantities():
                form.add_error(None, "Enter the quantity that arrived on at least one line.")
            elif purchase_order.remaining_amount > 0 and form.cleaned_data["answer"] is None:
                form.add_error("answer", "Please answer — is the remaining amount paid?")

        if form.errors:
            return self._render(request, purchase_order, form)

        try:
            receive_purchase_order(
                actor=request.user,
                purchase_order_id=purchase_order.pk,
                quantities=form.quantities(),
                paid_remaining=bool(form.cleaned_data["answer"]),
                amount=form.cleaned_data["amount"],
                paid_at=form.cleaned_data["paid_at"],
                proof=form.cleaned_data["proof"],
                note=form.cleaned_data["note"],
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
            return self._render(request, purchase_order, form)

        messages.success(request, "Receipt confirmed — your stock has been updated.")
        return redirect("distributor-purchase-order-detail", pk=purchase_order.pk)


class DeclinePurchaseOrderView(OwnerRequiredMixin, View):
    def post(self, request, pk):
        form = DeclinePurchaseOrderForm(request.POST)

        if not form.is_valid():
            messages.error(request, "A comment is required to decline a purchase order.")
            return redirect("owner-purchase-order-detail", pk=pk)

        try:
            decline_purchase_order(
                actor=request.user,
                purchase_order_id=pk,
                comment=form.cleaned_data["comment"],
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("owner-purchase-order-detail", pk=pk)

        messages.success(request, "Purchase order declined.")
        return redirect("owner-purchase-order-detail", pk=pk)


class CommentPurchaseOrderView(OwnerRequiredMixin, View):
    def post(self, request, pk):
        form = OwnerCommentForm(request.POST)

        if not form.is_valid():
            messages.error(request, "Could not save comment.")
            return redirect("owner-purchase-order-detail", pk=pk)

        try:
            add_owner_comment(
                actor=request.user,
                purchase_order_id=pk,
                comment=form.cleaned_data["comment"],
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("owner-purchase-order-detail", pk=pk)

        messages.success(request, "Comment saved.")
        return redirect("owner-purchase-order-detail", pk=pk)


class RecordPaymentView(DistributorRequiredMixin, View):
    def post(self, request, pk):
        form = RecordPaymentForm(request.POST, request.FILES)

        if not form.is_valid():
            messages.error(request, "Please check the payment details and try again.")
            return redirect("distributor-purchase-order-detail", pk=pk)

        purchase_order = _get_distributor_order(request, pk)

        try:
            record_payment(
                actor=request.user,
                purchase_order_id=pk,
                amount=form.cleaned_data["amount"],
                paid_at=form.cleaned_data["paid_at"],
                proof=form.cleaned_data["proof"],
                # Before anything ships it's (still) the advance — e.g.
                # paying again after the Owner rejected one; after, it's
                # the final payment.
                kind=(
                    PurchaseOrderPayment.Kind.ADVANCE
                    if purchase_order.status == PurchaseOrder.Status.PENDING
                    else PurchaseOrderPayment.Kind.FINAL
                ),
                note=form.cleaned_data["note"],
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("distributor-purchase-order-detail", pk=pk)

        messages.success(request, "Payment recorded.")
        return redirect("distributor-purchase-order-detail", pk=pk)


class ConfirmPaymentView(OwnerRequiredMixin, View):
    def post(self, request, pk, payment_id):
        try:
            confirm_payment(actor=request.user, payment_id=payment_id)
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("owner-purchase-order-detail", pk=pk)

        messages.success(request, "Payment confirmed.")
        return redirect("owner-purchase-order-detail", pk=pk)


class RejectPaymentView(OwnerRequiredMixin, View):
    def post(self, request, pk, payment_id):
        form = RejectPaymentForm(request.POST)

        if not form.is_valid():
            messages.error(request, "A reason is required to reject a payment.")
            return redirect("owner-purchase-order-detail", pk=pk)

        try:
            reject_payment(
                actor=request.user,
                payment_id=payment_id,
                reason=form.cleaned_data["reason"],
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("owner-purchase-order-detail", pk=pk)

        messages.success(request, "Payment rejected.")
        return redirect("owner-purchase-order-detail", pk=pk)


class PurchaseOrderPDFView(LoginRequiredMixin, View):
    """The order as a document, in the brand's logo and colour — titled
    for the stage it's at: Request to Quote, Quotation, Purchase Order or
    Invoice."""

    def get(self, request, pk):
        purchase_order = (
            PurchaseOrder.objects
            .for_user(request.user)
            .select_related("distributor_profile", "agreement")
            .prefetch_related("items__product", "payments")
            .filter(pk=pk)
            .first()
        )

        if purchase_order is None:
            raise PermissionDenied(
                "Purchase order was not found in your permitted scope."
            )

        pdf_bytes = render_pdf(
            "requests/purchase_order_pdf.html",
            {
                "purchase_order": purchase_order,
                "brand": _brand(),
                "items": list(purchase_order.items.all()),
                "highlight_changes": purchase_order.is_quote,
            },
        )

        number = purchase_order.invoice_number or purchase_order.po_number
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        disposition = "attachment" if request.GET.get("download") else "inline"
        response["Content-Disposition"] = (
            f'{disposition}; filename="{number}.pdf"'
        )

        return response


class PurchaseOrderRecordPDFView(LoginRequiredMixin, View):
    """The complete record of one order as a PDF: the stages, the final
    lines with their price changes, payments and the whole conversation
    between the Owner and the Distributor."""

    def get(self, request, pk):
        purchase_order = (
            PurchaseOrder.objects
            .for_user(request.user)
            .select_related("distributor_profile", "agreement")
            .prefetch_related("items__product", "payments__created_by")
            .filter(pk=pk)
            .first()
        )

        if purchase_order is None:
            raise Http404

        pdf_bytes = render_pdf(
            "requests/purchase_order_record_pdf.html",
            {
                "purchase_order": purchase_order,
                "brand": _brand(),
                "steps": order_steps(purchase_order),
                "items": list(purchase_order.items.all()),
                "payments": list(purchase_order.payments.all()),
                "timeline": revision_timeline(purchase_order),
                "generated_at": timezone.now(),
            },
        )

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        disposition = "inline" if request.GET.get("view") else "attachment"
        response["Content-Disposition"] = (
            f'{disposition}; filename="Order-Record-{purchase_order.po_number}.pdf"'
        )
        return response
