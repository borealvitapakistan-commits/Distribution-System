import uuid
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View

from apps.accounts.mixins import OwnerRequiredMixin
from apps.distributors.models import DistributorProfile
from apps.products.models import Product

from .exports import (
    expenses_document,
    export_response,
    ledger_document,
    market_document,
    prices_document,
    product_finance_document,
    report_document,
)
from .forms import ExpenseForm, MarketResearchForm
from .models import Expense, FinanceSettings, MarketResearch, SaleEntry
from .services import (
    EXPENSE_FIELDS,
    PERIODS,
    RESEARCH_FIELDS,
    ZERO,
    Figures,
    _snapshot,
    first_activity_date,
    latest_sale_date,
    delete_expense,
    delete_market_research,
    period_bounds,
    period_report,
    product_finance_sheet,
    product_price_rows,
    save_expense,
    save_market_research,
    stock_on_hand_value,
    sync_sales_ledger,
    update_distributor_cut,
)


EXAMPLE_RETAIL = Decimal("1200")


def _error_text(exc):
    return " ".join(getattr(exc, "messages", [str(exc)]))


def _date_param(request, name, default=None):
    """A yyyy-mm-dd query parameter; anything missing or impossible
    (2026-02-31) falls back to default instead of failing the page."""
    value = request.GET.get(name)
    try:
        parsed = parse_date(value) if value else None
    except ValueError:
        parsed = None
    return parsed or default


def _uuid_param(request, name):
    """A UUID query parameter as a string, or "" when missing or garbled."""
    value = request.GET.get(name, "")
    try:
        return str(uuid.UUID(value)) if value else ""
    except ValueError:
        return ""


def _sale_filters(request):
    """The ledger filters shared by the Reports and Sales Ledger pages,
    validated: anything unknown is simply ignored."""
    kind = request.GET.get("kind", "")
    return {
        "kind": kind if kind in SaleEntry.Kind.values else "",
        "product_id": _uuid_param(request, "product"),
        "distributor_id": _uuid_param(request, "distributor"),
        "batch_number": request.GET.get("batch", "").strip(),
    }


def _filter_choices():
    """Only the channels, products, Distributors and batches that have
    ledger entries, so no filter option can only ever show nothing."""
    entries = SaleEntry.objects.all()
    kinds = set(entries.values_list("kind", flat=True).distinct())
    return {
        "kinds": [(value, label) for value, label in SaleEntry.Kind.choices if value in kinds],
        "products": list(
            Product.objects.filter(sale_entries__isnull=False).distinct().order_by("name")
        ),
        "distributors": list(
            DistributorProfile.objects.filter(sale_entries__isnull=False).distinct().order_by("name")
        ),
        "batches": sorted(
            set(entries.exclude(batch_number="").values_list("batch_number", flat=True))
        ),
    }


def _active_filters(filters, choices):
    """Human labels for the filters in force, e.g. ["Product: Biotin"]."""
    labels = []
    if filters["kind"]:
        labels.append("Type: " + SaleEntry.Kind(filters["kind"]).label)
    if filters["product_id"]:
        product = next((p for p in choices["products"] if str(p.pk) == filters["product_id"]), None)
        labels.append("Product: " + (product.name if product else "unknown"))
    if filters["distributor_id"]:
        distributor = next(
            (d for d in choices["distributors"] if str(d.pk) == filters["distributor_id"]), None
        )
        labels.append("Distributor: " + (distributor.name if distributor else "unknown"))
    if filters["batch_number"]:
        labels.append("Batch: " + filters["batch_number"])
    return labels


PERIOD_LABELS = {
    "daily": "Daily",
    "weekly": "Weekly",
    "monthly": "Monthly",
    "all": "All time",
    "custom": "Custom range",
}


def _filter_query(filters):
    """The sale filters as a query string, so moving to the previous or
    next period keeps them."""
    params = {
        "kind": filters["kind"],
        "product": filters["product_id"],
        "distributor": filters["distributor_id"],
        "batch": filters["batch_number"],
    }
    return urlencode({name: value for name, value in params.items() if value})


def _range_label(start, end):
    if start and end:
        return f"{start:%d %b %Y} to {end:%d %b %Y}"
    if start:
        return f"From {start:%d %b %Y}"
    if end:
        return f"Up to {end:%d %b %Y}"
    return "All dates"


class FinanceMixin(OwnerRequiredMixin):
    """Owner only. Pages that show ledger figures first add any stock
    movement the ledger hasn't recorded yet, so a number is never missing
    a sale. Every page can be downloaded: ?format=csv or ?format=pdf."""

    finance_tab = ""
    syncs_ledger = False

    def dispatch(self, request, *args, **kwargs):
        if (
            self.syncs_ledger
            and request.method == "GET"
            and request.user.is_authenticated
            and getattr(request.user, "is_owner", False)
        ):
            with transaction.atomic():
                sync_sales_ledger(actor=request.user)
        return super().dispatch(request, *args, **kwargs)

    def render_page(self, request, template, context):
        query = request.GET.copy()
        query.pop("page", None)
        query.pop("format", None)
        context["finance_tab"] = self.finance_tab
        context["export_query"] = query.urlencode()
        return render(request, template, context)


class OwnerFinanceView(FinanceMixin, View):
    """Daily / weekly / monthly (or any date range) profit and loss."""

    finance_tab = "reports"
    syncs_ledger = True

    def get(self, request):
        today = timezone.localdate()
        period = request.GET.get("period", "monthly")
        if period not in PERIODS:
            period = "monthly"
        anchor = _date_param(request, "date", today)

        if period == "all":
            start, end, previous, following = period_bounds(
                "custom", today, start=first_activity_date(), end=today
            )
        else:
            start, end, previous, following = period_bounds(
                period,
                anchor,
                start=_date_param(request, "start"),
                end=_date_param(request, "end"),
            )

        filters = _sale_filters(request)
        choices = _filter_choices()
        active = _active_filters(filters, choices)
        report = period_report(start, end, **filters)
        stock = stock_on_hand_value()

        download = export_response(
            request, lambda: report_document(report, stock, period, active)
        )
        if download:
            return download

        latest = latest_sale_date(**filters) if not report.entries else None

        return self.render_page(
            request,
            "finance/dashboard.html",
            {
                "period": period,
                "periods": [(p, PERIOD_LABELS[p]) for p in PERIODS],
                "filter_query": _filter_query(filters),
                "anchor": anchor,
                "previous": previous,
                "following": following,
                "today": today,
                "report": report,
                "totals": report.totals,
                "timeline_unit": "month" if (end - start).days > 62 else "day",
                "stock": stock,
                "columns": report.columns,
                "filters": filters,
                "active_filters": active,
                "latest_sale": latest,
                **choices,
            },
        )


class ProductFinanceView(FinanceMixin, View):
    """The Product Finance sheet: every batch bought, priced, with
    expenses shared across it, down to profit per unit."""

    finance_tab = "product-finance"

    def get(self, request):
        settings = FinanceSettings.load()
        start = _date_param(request, "start")
        end = _date_param(request, "end")
        group = request.GET.get("group", "batch")
        if group not in ("batch", "product"):
            group = "batch"
        include_pending = request.GET.get("pending", "1") == "1"
        product_id = _uuid_param(request, "product")

        rows, totals = product_finance_sheet(
            start=start,
            end=end,
            group=group,
            include_pending=include_pending,
            cut=settings.distributor_cut_percentage,
            product_id=product_id,
        )
        has_market = any(row.market_price is not None for row in rows)

        download = export_response(
            request,
            lambda: product_finance_document(
                rows, totals, settings,
                group=group, start=start, end=end, include_pending=include_pending,
                has_market=has_market,
            ),
        )
        if download:
            return download

        example_cut = EXAMPLE_RETAIL * settings.distributor_cut_percentage / 100

        return self.render_page(
            request,
            "finance/product_finance.html",
            {
                "rows": rows,
                "totals": totals,
                "settings": settings,
                "example_cut": example_cut,
                "example_receive": EXAMPLE_RETAIL - example_cut,
                "start": start,
                "end": end,
                "group": group,
                "include_pending": include_pending,
                "product_id": product_id,
                "products": (
                    Product.objects.filter(manufacturer_order_items__batch__isnull=False)
                    .distinct().order_by("name")
                ),
                "has_market": has_market,
            },
        )

    def post(self, request):
        try:
            update_distributor_cut(
                actor=request.user,
                percentage=request.POST.get("distributor_cut_percentage", ""),
            )
            messages.success(request, "Distributor cut updated.")
        except ValidationError as exc:
            messages.error(request, _error_text(exc))
        return redirect(f"{request.path}?{request.GET.urlencode()}")


class SalesLedgerView(FinanceMixin, View):
    """Every finance ledger entry, filterable and downloadable."""

    finance_tab = "ledger"
    syncs_ledger = True

    def get(self, request):
        entries = (
            SaleEntry.objects.for_user(request.user)
            .select_related("product", "distributor_profile", "source_batch")
            .order_by("-occurred_at")
        )
        filters = _sale_filters(request)
        choices = _filter_choices()
        active = _active_filters(filters, choices)
        start = _date_param(request, "start")
        end = _date_param(request, "end")

        if filters["kind"]:
            entries = entries.filter(kind=filters["kind"])
        if filters["product_id"]:
            entries = entries.filter(product_id=filters["product_id"])
        if filters["distributor_id"]:
            entries = entries.filter(distributor_profile_id=filters["distributor_id"])
        if filters["batch_number"]:
            entries = entries.filter(batch_number=filters["batch_number"])
        if start:
            entries = entries.filter(occurred_on__gte=start)
        if end:
            entries = entries.filter(occurred_on__lte=end)

        download = export_response(
            request,
            lambda: ledger_document(entries, " · ".join([_range_label(start, end)] + active)),
        )
        if download:
            return download

        summary = Figures()
        for entry in entries:
            summary.add(entry)

        page = Paginator(entries, 50).get_page(request.GET.get("page"))

        return self.render_page(
            request,
            "finance/ledger.html",
            {
                "page_obj": page,
                "entries": page.object_list,
                "filters": filters,
                "active_filters": active,
                "summary": summary,
                "start": start,
                "end": end,
                **choices,
            },
        )


class ExpenseListView(FinanceMixin, View):
    finance_tab = "expenses"

    def get(self, request):
        expenses = Expense.objects.for_user(request.user)
        start = _date_param(request, "start")
        end = _date_param(request, "end")
        if start:
            expenses = expenses.filter(date__gte=start)
        if end:
            expenses = expenses.filter(date__lte=end)

        expenses = list(expenses)
        total = sum((expense.amount for expense in expenses), ZERO)

        download = export_response(
            request, lambda: expenses_document(expenses, total, _range_label(start, end))
        )
        if download:
            return download

        return self.render_page(
            request,
            "finance/expense_list.html",
            {
                "expenses": expenses,
                "total": total,
                "start": start,
                "end": end,
            },
        )


class ExpenseFormView(FinanceMixin, View):
    finance_tab = "expenses"

    def _expense(self, pk):
        return get_object_or_404(Expense.objects.for_user(self.request.user), pk=pk) if pk else None

    def _render(self, request, form, expense, service_error=None):
        return self.render_page(
            request,
            "finance/expense_form.html",
            {"form": form, "expense": expense, "service_error": service_error},
        )

    def get(self, request, pk=None):
        expense = self._expense(pk)
        return self._render(request, ExpenseForm(instance=expense), expense)

    def post(self, request, pk=None):
        expense = self._expense(pk)
        before = _snapshot(expense, EXPENSE_FIELDS) if expense else None
        form = ExpenseForm(request.POST, instance=expense)

        if not form.is_valid():
            return self._render(request, form, expense)

        try:
            save_expense(actor=request.user, expense=form.instance, before=before)
        except ValidationError as exc:
            return self._render(request, form, expense, _error_text(exc))

        messages.success(request, "Expense saved.")
        return redirect("owner-finance-expenses")


class ExpenseDeleteView(FinanceMixin, View):
    def post(self, request, pk):
        expense = get_object_or_404(Expense.objects.for_user(request.user), pk=pk)
        delete_expense(actor=request.user, expense=expense)
        messages.success(request, "Expense deleted.")
        return redirect("owner-finance-expenses")


class ProductPricesView(FinanceMixin, View):
    finance_tab = "prices"

    def get(self, request):
        rows = product_price_rows()
        # Sizes we've neither been quoted nor ordered have no Manufacturer
        # price to show — hidden unless asked for.
        show_all = request.GET.get("all") == "1"
        priced = [row for row in rows if row.manufacturer_price is not None]
        hidden = 0 if show_all else len(rows) - len(priced)
        if not show_all:
            rows = priced
        columns = {
            "pack": any(row.pack_label for row in rows),
            "market": any(row.market_price is not None for row in rows),
        }

        download = export_response(request, lambda: prices_document(rows, columns))
        if download:
            return download

        return self.render_page(
            request,
            "finance/product_prices.html",
            {"rows": rows, "columns": columns, "show_all": show_all, "hidden": hidden},
        )


class MarketResearchListView(FinanceMixin, View):
    finance_tab = "market"

    def get(self, request):
        rows = list(
            MarketResearch.objects.for_user(request.user)
            .select_related("product")
        )

        download = export_response(request, lambda: market_document(rows))
        if download:
            return download

        return self.render_page(request, "finance/market_research_list.html", {"rows": rows})


class MarketResearchFormView(FinanceMixin, View):
    finance_tab = "market"

    def _research(self, pk):
        return (
            get_object_or_404(MarketResearch.objects.for_user(self.request.user), pk=pk)
            if pk else None
        )

    def _render(self, request, form, research, service_error=None):
        return self.render_page(
            request,
            "finance/market_research_form.html",
            {"form": form, "research": research, "service_error": service_error},
        )

    def get(self, request, pk=None):
        research = self._research(pk)
        initial = {}
        if research is None and request.GET.get("product"):
            initial["product"] = request.GET["product"]
        return self._render(request, MarketResearchForm(instance=research, initial=initial), research)

    def post(self, request, pk=None):
        research = self._research(pk)
        before = _snapshot(research, RESEARCH_FIELDS) if research else None
        form = MarketResearchForm(request.POST, instance=research)

        if not form.is_valid():
            return self._render(request, form, research)

        try:
            save_market_research(actor=request.user, research=form.instance, before=before)
        except ValidationError as exc:
            return self._render(request, form, research, _error_text(exc))

        messages.success(request, "Market research saved.")
        return redirect("owner-finance-market")


class MarketResearchDeleteView(FinanceMixin, View):
    def post(self, request, pk):
        research = get_object_or_404(MarketResearch.objects.for_user(request.user), pk=pk)
        delete_market_research(actor=request.user, research=research)
        messages.success(request, "Market research deleted.")
        return redirect("owner-finance-market")
