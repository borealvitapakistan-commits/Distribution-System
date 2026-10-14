"""Downloads for every finance page. Each page describes its tables once
as an ExportDocument — the same rows and totals it shows on screen — and
that one description is written out as CSV (opens in Excel, numbers left
as plain numbers so they can be summed) or as a PDF."""

import csv
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from django.http import HttpResponse
from django.utils import timezone

from apps.core.models import Brand
from apps.core.utils import render_pdf

TEXT, MONEY, QTY, PCT, SHARE = "text", "money", "qty", "pct", "share"


@dataclass
class Section:
    heading: str
    columns: list  # [(label, kind)]
    rows: list = field(default_factory=list)
    footer: list | None = None
    note: str = ""
    pdf_columns: list | None = None  # column numbers the PDF keeps; None = all
    csv_columns: list | None = None  # column numbers the CSV keeps; None = all

    def without(self, *labels):
        """This section minus the named columns — for columns with
        nothing in them, which the page leaves out too."""
        keep = [index for index, (label, _kind) in enumerate(self.columns) if label not in labels]
        if len(keep) == len(self.columns):
            return self
        pick = lambda values: [values[index] for index in keep]
        return Section(
            heading=self.heading,
            columns=pick(self.columns),
            rows=[pick(row) for row in self.rows],
            footer=pick(self.footer) if self.footer else None,
            note=self.note,
        )


@dataclass
class ExportDocument:
    title: str
    filename: str
    subtitle: str = ""
    sections: list = field(default_factory=list)


def _quantized(value, places):
    return Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _plain(value, kind):
    """For CSV: numbers without grouping, so Excel reads them as numbers."""
    if value is None or value == "":
        return ""
    if kind == MONEY:
        return str(_quantized(value, 2))
    if kind == QTY:
        number = Decimal(value)
        return str(number.quantize(Decimal(1))) if number == number.to_integral_value() else f"{number.normalize():f}"
    if kind == PCT:
        return str(_quantized(value, 1))
    if kind == SHARE:
        return str(_quantized(Decimal(value) * 100, 2))
    return str(value)


def _pretty(value, kind):
    """For PDF: grouped like the screen."""
    if value is None or value == "":
        return "—" if kind != TEXT else ""
    if kind == MONEY:
        return f"{_quantized(value, 2):,.2f}"
    if kind == QTY:
        number = Decimal(value)
        return f"{number:,.0f}" if number == number.to_integral_value() else f"{number.normalize():,f}"
    if kind == PCT:
        return f"{_quantized(value, 1)}%"
    if kind == SHARE:
        return f"{_quantized(Decimal(value) * 100, 1)}%"
    return str(value)


def _header(label, kind):
    return f"{label} (%)" if kind in (PCT, SHARE) else label


def csv_response(document):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{document.filename}.csv"'
    response.write("﻿")  # so Excel reads the file as UTF-8 (Rs, — and × survive)
    writer = csv.writer(response)

    writer.writerow([document.title])
    if document.subtitle:
        writer.writerow([document.subtitle])
    writer.writerow([f"Downloaded {timezone.localtime():%d %b %Y %H:%M}"])

    for section in document.sections:
        writer.writerow([])
        writer.writerow([section.heading])
        keep = section.csv_columns or range(len(section.columns))
        columns = [section.columns[index] for index in keep]

        def values(row):
            return [_plain(row[index], kind) for index, (_label, kind) in zip(keep, columns)]

        writer.writerow([_header(label, kind) for label, kind in columns])
        for row in section.rows:
            writer.writerow(values(row))
        if section.footer:
            writer.writerow(values(section.footer))
        if section.note:
            writer.writerow([section.note])

    return response


def pdf_response(document):
    sections = []
    for section in document.sections:
        keep = section.pdf_columns or range(len(section.columns))
        columns = [section.columns[index] for index in keep]

        def cells(values):
            return [
                {"text": _pretty(values[index], kind), "numeric": kind != TEXT}
                for index, (_label, kind) in zip(keep, columns)
            ]

        sections.append({
            "heading": section.heading,
            "columns": [{"label": label, "numeric": kind != TEXT} for label, kind in columns],
            "rows": [cells(row) for row in section.rows],
            "footer": cells(section.footer) if section.footer else None,
            "note": section.note,
        })

    pdf = render_pdf(
        "finance/export_pdf.html",
        {
            "document": document,
            "sections": sections,
            "brand": Brand.objects.first(),
            "downloaded_at": timezone.localtime(),
            "wide": any(len(section["columns"]) > 7 for section in sections),
        },
    )
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{document.filename}.pdf"'
    return response


def export_response(request, build_document):
    """The download for ?format=csv / ?format=pdf, or None to show the
    page. build_document is only called when a download was asked for."""
    file_format = request.GET.get("format")
    if file_format == "csv":
        return csv_response(build_document())
    if file_format == "pdf":
        return pdf_response(build_document())
    return None


# ---------------------------------------------------------------------------
# One builder per page
# ---------------------------------------------------------------------------


FIGURE_COLUMNS = [
    ("Units sold", QTY),
    ("Retail value", MONEY),
    ("Distributor cut / discount", MONEY),
    ("Revenue", MONEY),
    ("Cost of goods", MONEY),
    ("Gross profit", MONEY),
    ("Gross margin", PCT),
    ("Free / written off (cost)", MONEY),
    ("Expenses", MONEY),
    ("Net profit", MONEY),
]


def _figure_values(figures):
    return [
        figures.units_sold,
        figures.retail_value,
        figures.cut,
        figures.revenue,
        figures.cogs,
        figures.gross_profit,
        figures.gross_margin,
        figures.lost_cost,
        figures.expenses,
        figures.net_profit,
    ]


def _figures_section(heading, first_column, groups, *, with_detail=False, footer_figures=None):
    columns = [(first_column, TEXT)]
    if with_detail:
        columns.append(("Detail", TEXT))
    columns += FIGURE_COLUMNS

    rows = []
    for group in groups:
        row = [group.label] + ([group.detail] if with_detail else [])
        rows.append(row + _figure_values(group.figures))

    footer = None
    if footer_figures is not None and rows:
        footer = ["Total"] + ([""] if with_detail else []) + _figure_values(footer_figures)

    return Section(heading=heading, columns=columns, rows=rows, footer=footer)


def _ledger_section(entries, heading="Entries"):
    return Section(
        heading=heading,
        columns=[
            ("Date", TEXT), ("Type", TEXT), ("Product", TEXT), ("SKU", TEXT),
            ("Bottle size", TEXT), ("Batch", TEXT), ("Distributor", TEXT),
            ("Quantity", QTY), ("Cost / unit", MONEY), ("Cost source", TEXT),
            ("Retail / unit", MONEY), ("We receive / unit", MONEY), ("Retail value", MONEY),
            ("Distributor cut / discount", MONEY), ("Revenue", MONEY), ("Cost", MONEY),
            ("Profit", MONEY), ("Reference", TEXT), ("Product / SKU", TEXT),
        ],
        # Date, Type, Product / SKU, Batch, Distributor, Qty, Cost / unit,
        # Retail / unit, We receive / unit, Revenue, Cost, Profit.
        pdf_columns=[0, 1, 18, 5, 6, 7, 8, 10, 11, 14, 15, 16],
        csv_columns=list(range(18)),
        rows=[
            [
                timezone.localtime(entry.occurred_at).strftime("%Y-%m-%d %H:%M"),
                entry.get_kind_display(),
                entry.product.name,
                entry.product.sku,
                entry.bottle_size or "",
                entry.batch_number,
                entry.distributor_profile.name if entry.distributor_profile else "",
                entry.quantity,
                entry.unit_cost,
                entry.get_cost_source_display(),
                entry.retail_unit_price,
                entry.net_unit_price,
                entry.retail_value,
                entry.cut,
                entry.revenue,
                entry.cost,
                entry.profit,
                entry.reference,
                f"{entry.product.name} ({entry.product.sku})",
            ]
            for entry in entries
        ],
    )


def report_document(report, stock, period, active_filters=()):
    totals = report.totals
    span = (
        f"{report.start:%d %b %Y}" if report.start == report.end
        else f"{report.start:%d %b %Y} to {report.end:%d %b %Y}"
    )

    statement = Section(
        heading="Profit & loss (Rs)",
        columns=[("Line", TEXT), ("Amount", MONEY)],
        rows=[
            ["Retail sale value (retail price × units sold)", totals.retail_value],
            ["- Distributor cut & Shopify discounts", totals.cut],
            ["Revenue (what we actually received)", totals.revenue],
            ["- Cost of goods sold (batch landed cost × units)", totals.cogs],
            ["Gross profit", totals.gross_profit],
            ["- Expenses", totals.expenses],
            [f"- Stock given free ({_pretty(totals.free_units, QTY)} units, at cost)", totals.free_cost],
            [f"- Stock written off ({_pretty(totals.written_off_units, QTY)} units, at cost)", totals.written_off_cost],
            ["Net profit", totals.net_profit],
        ],
        note=(
            f"{_pretty(totals.unknown_cost_units, QTY)} units had no Manufacturer price, so their cost counts as 0."
            if totals.unknown_cost_units else ""
        ),
    )
    cash = Section(
        heading="Investment & cash (Rs)",
        columns=[("Line", TEXT), ("Amount", MONEY)],
        rows=[
            [f"Stock bought (received, {_pretty(report.stock_bought_units, QTY)} units × landed cost)", report.stock_bought],
            ["Paid to Manufacturers", report.paid_to_manufacturers],
            ["Received from Distributors (confirmed)", report.received_from_distributors],
            ["Expenses paid", totals.expenses],
            [f"Stock on hand now, at cost ({_pretty(stock.units, QTY)} units)", stock.cost],
            ["Stock on hand now, at retail", stock.retail],
        ],
    )

    sections = [statement, cash]
    if report.expenses_by_category:
        sections.append(Section(
            heading="Expenses by category (Rs)",
            columns=[("Category", TEXT), ("Amount", MONEY)],
            rows=[[name, amount] for name, amount in report.expenses_by_category],
            footer=["Total", totals.expenses],
        ))
    figures = []
    if period != "daily":
        figures.append(_figures_section("Timeline", "Date", report.timeline, footer_figures=totals))
    figures += [
        _figures_section("By channel", "Channel", report.by_kind, footer_figures=totals),
        _figures_section("By product", "Product", report.by_product, with_detail=True, footer_figures=totals),
        _figures_section("By batch", "Batch", report.by_batch, with_detail=True, footer_figures=totals),
    ]
    if report.by_distributor:
        figures.append(_figures_section("By Distributor", "Distributor", report.by_distributor))

    empty = [
        label for label, has in (
            ("Distributor cut / discount", report.columns["cut"]),
            ("Free / written off (cost)", report.columns["lost"]),
            ("Expenses", report.columns["expenses"]),
        ) if not has
    ]
    sections += [section.without(*empty) for section in figures]
    sections.append(_ledger_section(report.entries, "Every entry in the period"))
    if report.expenses:
        sections.append(_expenses_section(report.expenses, totals.expenses))

    subtitle = " · ".join([span, *active_filters])
    if report.filtered:
        subtitle += " · expenses not included (they belong to the whole business)"

    return ExportDocument(
        title=f"Finance report — {'All time' if period == 'all' else period.title()}",
        subtitle=subtitle,
        filename=f"finance-{period}-report-{report.start:%Y%m%d}-{report.end:%Y%m%d}",
        sections=sections,
    )


def product_finance_document(
    rows, totals, settings, *, group, start, end, include_pending, has_market=True
):
    scope = []
    if start or end:
        scope.append(
            f"orders placed {start:%d %b %Y}" if start else "orders placed up to"
        )
        if end:
            scope.append(f"to {end:%d %b %Y}")
    else:
        scope.append("all orders")
    scope.append("ordered and received batches" if include_pending else "received batches only")
    scope.append(f"distributor cut {_pretty(settings.distributor_cut_percentage, PCT)}")

    section = Section(
        heading="Product Finance",
        columns=[
            ("Product", TEXT),
            ("Batch" if group == "batch" else "Batches", TEXT),
            ("Price to make / Manufacturer price", MONEY),
            ("Market price", MONEY),
            ("Our retail price (Rs)", MONEY),
            ("Quantity", QTY),
            ("Distributor cut / unit (Rs)", MONEY),
            ("We receive / unit (Rs)", MONEY),
            ("Expense share", SHARE),
            ("Expense / unit (Rs)", MONEY),
            ("Profit / unit (Rs)", MONEY),
            ("Profit (of retail)", PCT),
            ("Total profit (Rs)", MONEY),
        ],
        rows=[
            [
                row.label,
                (
                    f"{row.batches[0].code} ({row.batches[0].manufacturer_order_item.order.po_number})"
                    if group == "batch" else f"{len(row.batches)} batches"
                ),
                row.unit_cost,
                row.market_price,
                row.retail_price,
                row.quantity,
                row.distributor_cut,
                row.we_receive,
                row.expense_share,
                row.expense_per_unit,
                row.profit_per_unit,
                row.profit_percentage,
                row.total_profit,
            ]
            for row in rows
        ],
        footer=[
            "Totals", "", totals.investment, None, totals.retail_value, totals.quantity,
            totals.distributor_cut, totals.we_receive, totals.expense_share, totals.expenses,
            None, totals.net_margin, totals.net_profit,
        ],
        note=(
            "Totals of money columns are per-unit figures × quantity: Price to make = investment, "
            "Expense / unit = total expenses, Total profit = gross profit - expenses."
        ),
    )
    summary = Section(
        heading="Summary (Rs)",
        columns=[("Line", TEXT), ("Amount", MONEY)],
        rows=[
            ["Investment (manufacturer price × quantity)", totals.investment],
            ["Retail sale value (retail price × quantity)", totals.retail_value],
            ["- Distributor cut", totals.distributor_cut],
            ["We receive", totals.we_receive],
            ["Gross profit (we receive - investment)", totals.gross_profit],
            ["- Expenses", totals.expenses],
            ["Total profit", totals.net_profit],
        ],
    )
    if not has_market:
        section = section.without("Market price")
    return ExportDocument(
        title="Product Finance",
        subtitle=", ".join(scope),
        filename=f"product-finance-{timezone.localdate():%Y%m%d}",
        sections=[summary, section],
    )


def ledger_document(entries, subtitle=""):
    return ExportDocument(
        title="Sales Ledger",
        subtitle=subtitle,
        filename=f"finance-ledger-{timezone.localdate():%Y%m%d}",
        sections=[_ledger_section(entries)],
    )


def _expenses_section(expenses, total):
    return Section(
        heading="Expenses",
        columns=[("Date", TEXT), ("Expense name", TEXT), ("Category", TEXT), ("Amount (Rs)", MONEY), ("Comments", TEXT)],
        rows=[
            [f"{expense.date:%Y-%m-%d}", expense.name, expense.get_category_display(), expense.amount, expense.comments]
            for expense in expenses
        ],
        footer=["", "TOTAL EXPENSES", "", total, ""],
    )


def expenses_document(expenses, total, subtitle=""):
    return ExportDocument(
        title="Expenses",
        subtitle=subtitle,
        filename=f"expenses-{timezone.localdate():%Y%m%d}",
        sections=[_expenses_section(expenses, total)],
    )


def prices_document(rows, columns=None):
    columns = columns or {"pack": True, "market": True}
    section = Section(
        heading="Product Prices",
        columns=[
            ("Product", TEXT), ("SKU", TEXT), ("Pack size", TEXT), ("Manufacturer", TEXT),
            ("Manufacturer price (Rs)", MONEY), ("Price from", TEXT),
            ("Latest batch cost, landed (Rs)", MONEY), ("Latest batch", TEXT),
            ("Our retail price (Rs)", MONEY), ("Markup on cost", PCT),
            ("Market price (Rs)", MONEY), ("Retail vs market", PCT), ("Dropdown name", TEXT),
        ],
        rows=[
            [
                row.product.name, row.product.sku, row.pack_label, row.manufacturer_name,
                row.manufacturer_price, row.price_source, row.latest_batch_cost,
                row.latest_batch.code if row.latest_batch else "",
                row.retail_price, row.markup_percentage, row.market_price,
                row.vs_market_percentage, row.dropdown_name,
            ]
            for row in rows
        ],
    )
    empty = []
    if not columns["pack"]:
        empty.append("Pack size")
    if not columns["market"]:
        empty += ["Market price (Rs)", "Retail vs market"]
    return ExportDocument(
        title="Product Prices",
        filename=f"product-prices-{timezone.localdate():%Y%m%d}",
        sections=[section.without(*empty)],
    )


def market_document(rows):
    return ExportDocument(
        title="Market Research",
        filename=f"market-research-{timezone.localdate():%Y%m%d}",
        sections=[
            Section(
                heading="Market Research",
                columns=[
                    ("S.No", TEXT), ("Our product", TEXT), ("Similar product on market", TEXT),
                    ("Store", TEXT), ("Pack size", QTY), ("Unit", TEXT), ("Market price (Rs)", MONEY),
                    ("Price per unit (Rs)", MONEY), ("Use for market price?", TEXT), ("Note", TEXT),
                    ("Checked", TEXT), ("Link", TEXT),
                ],
                rows=[
                    [
                        number, row.product.name, row.similar_product, row.store, row.pack_size,
                        row.unit, row.market_price, row.price_per_unit,
                        "Yes" if row.use_for_market_price else "No", row.note,
                        f"{row.checked_on:%Y-%m-%d}", row.link,
                    ]
                    for number, row in enumerate(rows, start=1)
                ],
            )
        ],
    )
