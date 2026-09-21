from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from apps.accounts.mixins import (
    DistributorRequiredMixin,
    OwnerRequiredMixin,
)
from apps.core.utils import render_pdf

from .forms import (
    DeclinePurchaseOrderForm,
    OwnerCommentForm,
    PricingForm,
    PurchaseOrderItemFormSet,
    ReceivePurchaseOrderItemForm,
    RecordPaymentForm,
    RejectPaymentForm,
    ShipPurchaseOrderItemForm,
)
from .models import PurchaseOrder, PurchaseOrderItem
from .services import (
    add_owner_comment,
    confirm_payment,
    create_purchase_order,
    decline_purchase_order,
    mark_purchase_order_viewed,
    receive_purchase_order_item,
    record_payment,
    reject_payment,
    ship_purchase_order_item,
    update_pricing,
)


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
                "requested_price": form.cleaned_data.get("requested_price"),
            }
        )

    return rows


class PurchaseOrderCreateView(DistributorRequiredMixin, View):
    template_name = "requests/purchase_order_form.html"

    def get(self, request):
        formset = PurchaseOrderItemFormSet(instance=PurchaseOrder())

        return render(
            request,
            self.template_name,
            {"formset": formset},
        )

    def post(self, request):
        formset = PurchaseOrderItemFormSet(
            request.POST,
            instance=PurchaseOrder(),
        )

        if not formset.is_valid():
            return render(
                request,
                self.template_name,
                {"formset": formset},
            )

        try:
            create_purchase_order(
                actor=request.user,
                items=purchase_order_item_rows(formset),
            )
        except (PermissionDenied, ValidationError) as exc:
            return render(
                request,
                self.template_name,
                {"formset": formset, "service_error": exc},
            )

        messages.success(request, "Purchase order submitted successfully.")
        return redirect("distributor-purchase-order-list")


class DistributorOrdersHubView(DistributorRequiredMixin, TemplateView):
    template_name = "requests/distributor_orders_hub.html"


class DistributorPurchaseOrderListView(DistributorRequiredMixin, ListView):
    template_name = "requests/distributor_purchase_order_list.html"
    context_object_name = "purchase_orders"

    def get_queryset(self):
        return (
            PurchaseOrder.objects
            .for_user(self.request.user)
            .prefetch_related("items__product")
        )


class DistributorPurchaseOrderDetailView(DistributorRequiredMixin, DetailView):
    template_name = "requests/distributor_purchase_order_detail.html"
    context_object_name = "purchase_order"

    def get_queryset(self):
        return (
            PurchaseOrder.objects
            .for_user(self.request.user)
            .prefetch_related("items__product", "payments")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["receive_form"] = ReceivePurchaseOrderItemForm()
        context["payment_form"] = RecordPaymentForm()
        return context


class OwnerPurchaseOrdersHubView(OwnerRequiredMixin, TemplateView):
    template_name = "requests/owner_purchase_orders_hub.html"


class OwnerPurchaseOrderListView(OwnerRequiredMixin, ListView):
    template_name = "requests/owner_purchase_order_list.html"
    context_object_name = "purchase_orders"

    def get_queryset(self):
        return (
            PurchaseOrder.objects
            .for_user(self.request.user)
            .select_related("distributor_profile")
            .prefetch_related("items")
        )


class OwnerPurchaseOrderDetailView(OwnerRequiredMixin, DetailView):
    template_name = "requests/owner_purchase_order_detail.html"
    context_object_name = "purchase_order"

    def get_queryset(self):
        return (
            PurchaseOrder.objects
            .for_user(self.request.user)
            .select_related("distributor_profile")
            .prefetch_related(
                "items__product",
                "items__manufacturer_order_items__order",
                "payments",
            )
        )

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.was_new = self.object.owner_viewed_at is None
        mark_purchase_order_viewed(actor=request.user, purchase_order=self.object)
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["items_with_ship_forms"] = [
            (item, ShipPurchaseOrderItemForm(product=item.product))
            for item in self.object.items.all()
        ]
        context["decline_form"] = DeclinePurchaseOrderForm()
        context["comment_form"] = OwnerCommentForm(
            initial={"comment": self.object.owner_comment}
        )
        context["pricing_form"] = PricingForm(instance=self.object)
        context["reject_payment_form"] = RejectPaymentForm()
        context["was_new"] = getattr(self, "was_new", False)
        return context


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


class ReceivePurchaseOrderItemView(DistributorRequiredMixin, View):
    def post(self, request, pk, item_id):
        form = ReceivePurchaseOrderItemForm(request.POST)

        if not form.is_valid():
            messages.error(request, "Please provide a valid quantity.")
            return redirect("distributor-purchase-order-detail", pk=pk)

        try:
            receive_purchase_order_item(
                actor=request.user,
                item_id=item_id,
                quantity=form.cleaned_data["quantity"],
            )
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("distributor-purchase-order-detail", pk=pk)

        messages.success(request, "Receipt confirmed — your stock has been updated.")
        return redirect("distributor-purchase-order-detail", pk=pk)


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

        try:
            record_payment(
                actor=request.user,
                purchase_order_id=pk,
                amount=form.cleaned_data["amount"],
                paid_at=form.cleaned_data["paid_at"],
                proof=form.cleaned_data["proof"],
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
    def get(self, request, pk):
        purchase_order = (
            PurchaseOrder.objects
            .for_user(request.user)
            .select_related("distributor_profile")
            .prefetch_related("items__product", "payments")
            .filter(pk=pk)
            .first()
        )

        if purchase_order is None:
            raise PermissionDenied(
                "Purchase order was not found in your permitted scope."
            )

        from apps.core.models import Brand

        pdf_bytes = render_pdf(
            "requests/purchase_order_pdf.html",
            {
                "purchase_order": purchase_order,
                "brand": Brand.objects.first(),
            },
        )

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        disposition = "attachment" if request.GET.get("download") else "inline"
        response["Content-Disposition"] = (
            f'{disposition}; filename="{purchase_order.po_number}.pdf"'
        )

        return response
