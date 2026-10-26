from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Brand
from apps.distributors.models import DistributorProfile
from apps.distributors.services import approve_distributor, create_distributor
from apps.manufacturers.services import (
    create_manufacturer,
    create_manufacturer_order,
    mark_manufacturer_order_received,
    record_manufacturer_invoice,
)
from apps.owner_inventory.models import ShopifyOrder, ShopifyOrderLine, StockBatch, StockMovement
from apps.owner_inventory.services import give_to_distributor
from apps.owner_inventory.shopify_sync import post_line
from apps.owner_warehouse.models import Location
from apps.owner_warehouse.services import create_inventory, create_location
from apps.products.models import ProductPackagePrice, ProductRetailPrice
from apps.products.services import create_product
from apps.requests.services import create_purchase_order, ship_purchase_order_item

from .models import Expense, MarketResearch, SaleEntry
from .services import (
    SheetRow,
    compute_product_finance,
    market_price_for,
    period_bounds,
    period_report,
    product_finance_sheet,
    product_price_rows,
    sync_sales_ledger,
)


def _row(cost, retail, quantity=1000):
    return SheetRow(
        product=None,
        bottle_size=None,
        unit_cost=Decimal(cost),
        retail_price=Decimal(retail),
        quantity=Decimal(quantity),
    )


class ProductFinanceFormulaTests(TestCase):
    """The figures from the owner's Financial_sheet_3.xlsx (rows 17-23,
    60% distributor cut, Rs 1,520,400 of expenses, Rs 3,734,600 of gross
    profit across the whole sheet) come out the same here."""

    def setUp(self):
        self.rows = [
            _row(120, 500),
            _row(390, 1800),
            _row(200, 1000),
            _row(10, 160),
            _row(20, 395),
            _row(30, 350),
            _row(40, 160),
        ]
        # The sheet's other rows, folded into one: their gross profit
        # takes the whole sheet's to Rs 3,734,600.
        self.rows.append(_row(0, Decimal("2798600") / Decimal("0.4"), 1))
        self.totals = compute_product_finance(
            self.rows,
            total_expenses=Decimal("1520400"),
            distributor_cut_percentage=Decimal("60"),
        )

    def test_distributor_cut_and_we_receive(self):
        row = self.rows[0]
        self.assertEqual(row.distributor_cut, Decimal("300"))
        self.assertEqual(row.we_receive, Decimal("200"))
        self.assertEqual(self.rows[1].distributor_cut, Decimal("1080"))
        self.assertEqual(self.rows[1].we_receive, Decimal("720"))

    def test_expense_share_profit_and_total_profit_match_the_sheet(self):
        expected = [
            # share %, expense / unit, profit / unit, profit %, total profit
            ("2.1", 33, 47, "9.5", 47431),
            ("8.8", 134, 196, "10.9", 195653),
            ("5.4", 81, 119, "11.9", 118578),
            ("1.4", 22, 32, "20.0", 32016),
            ("3.7", 56, 82, "20.7", 81819),
            ("2.9", 45, 65, "18.6", 65218),
            ("0.6", 10, 14, "8.9", 14229),
        ]
        for row, (share, expense, profit, profit_pct, total) in zip(self.rows, expected):
            self.assertEqual((row.expense_share * 100).quantize(Decimal("0.1")), Decimal(share))
            self.assertEqual(row.expense_per_unit.quantize(Decimal("1")), expense)
            self.assertEqual(row.profit_per_unit.quantize(Decimal("1")), profit)
            self.assertEqual(row.profit_percentage.quantize(Decimal("0.1")), Decimal(profit_pct))
            self.assertEqual(row.total_profit.quantize(Decimal("1")), total)

    def test_totals(self):
        self.assertEqual(self.totals.gross_profit, Decimal("3734600"))
        self.assertEqual(self.totals.expense_share.quantize(Decimal("0.0001")), Decimal("1"))
        self.assertEqual(self.totals.net_profit.quantize(Decimal("1")), Decimal("2214200"))

    def test_loss_making_rows_carry_no_expenses(self):
        rows = [_row(100, 500), _row(400, 500)]
        totals = compute_product_finance(
            rows, total_expenses=Decimal("1000"), distributor_cut_percentage=Decimal("60")
        )
        self.assertEqual(rows[0].expense_share, Decimal("1"))
        self.assertEqual(rows[1].expense_share, Decimal("0"))
        self.assertEqual(totals.net_profit, totals.gross_profit - Decimal("1000"))

    def test_market_price_is_price_per_unit_times_our_pack(self):
        product = SimpleNamespace(pk=1, size=None)
        research = [
            SimpleNamespace(product_id=1, use_for_market_price=True,
                            price_per_unit=Decimal("850") / 30, market_price=Decimal("850")),
            SimpleNamespace(product_id=1, use_for_market_price=False,
                            price_per_unit=Decimal("100"), market_price=Decimal("3000")),
        ]
        self.assertEqual(market_price_for(product, 60, research), Decimal("1700.00"))

    def test_period_bounds(self):
        day = timezone.localdate().replace(year=2026, month=10, day=14)
        self.assertEqual(period_bounds("weekly", day)[:2], (day - timedelta(days=2), day + timedelta(days=4)))
        start, end, previous, following = period_bounds("monthly", day)
        self.assertEqual((start.day, end.day), (1, 31))
        self.assertEqual((previous.month, following.month), (9, 11))


class FinanceLedgerTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@finance.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.brand = Brand.objects.create(name="Boreal Vita")
        self.manufacturer = create_manufacturer(actor=self.owner, name="Glorious Laboratories")
        self.product = create_product(
            actor=self.owner,
            sku="FIN-SAW",
            barcode="",
            name="Saw Palmetto",
            base_retail_price=Decimal("500.00"),
            currency="pkr",
        )
        ProductRetailPrice.objects.create(product=self.product, bottle_size=60, price=Decimal("900.00"))
        inventory = create_inventory(actor=self.owner, code="FIN", name="Finance Region")
        self.warehouse = create_location(
            actor=self.owner,
            code="FIN-WH",
            name="Finance Warehouse",
            location_type=Location.LocationType.OWN,
            inventory=inventory,
        )

        # 100 bottles of 60 at Rs 200, 10% tax, Rs 1,000 shipping:
        # landed cost = 200 × 1.1 + 1,000 × 200 ÷ 20,000 = Rs 230.
        order = create_manufacturer_order(
            actor=self.owner,
            manufacturer=self.manufacturer,
            brand=self.brand,
            tax_percentage=Decimal("10"),
            shipping_amount=Decimal("1000"),
            items=[{
                "product": self.product,
                "bottle_size": 60,
                "quantity": Decimal("100"),
                "unit_price": Decimal("200.00"),
            }],
        )
        mark_manufacturer_order_received(actor=self.owner, order_id=order.pk)
        record_manufacturer_invoice(actor=self.owner, order_id=order.pk, to_location=self.warehouse)
        self.batch = StockBatch.objects.get(location=self.warehouse, product=self.product)

        distributor_user = create_distributor(
            actor=self.owner,
            email="dist@finance.test",
            temporary_password="TemporaryPassword123!",
            name="Karachi Distributors",
        )
        approve_distributor(user=self.owner, distributor_id=distributor_user.pk)
        distributor_user.refresh_from_db()
        self.distributor_user = distributor_user
        self.distributor = DistributorProfile.objects.get(user=distributor_user)

    def _ship(self, quantity, unit_price):
        po = create_purchase_order(
            actor=self.distributor_user,
            items=[{"product": self.product, "quantity_requested": Decimal(quantity)}],
        )
        item = po.items.get()
        item.unit_price = Decimal(unit_price)
        item.save(update_fields=["unit_price"])
        ship_purchase_order_item(
            actor=self.owner, item_id=item.pk, allocations=[(self.batch, Decimal(quantity))]
        )
        self.batch.refresh_from_db()
        return item

    def test_distributor_shipment_records_a_sale_at_landed_cost(self):
        item = self._ship(10, "200.00")

        entry = SaleEntry.objects.get(kind=SaleEntry.Kind.DISTRIBUTOR_SALE)
        self.assertEqual(entry.purchase_order_item, item)
        self.assertEqual(entry.distributor_profile, self.distributor)
        self.assertEqual(entry.unit_cost, Decimal("230.0000"))
        self.assertEqual(entry.cost_source, SaleEntry.CostSource.BATCH)
        self.assertEqual(entry.bottle_size, 60)
        self.assertEqual(entry.retail_unit_price, item.list_price)
        self.assertEqual(entry.net_unit_price, Decimal("200.00"))
        self.assertEqual(entry.batch_number, self.batch.batch_number)
        self.assertEqual(entry.source_batch, self.batch.source_batch)

    def test_receiving_the_shipment_does_not_count_the_sale_twice(self):
        from apps.requests.services import receive_purchase_order_item

        item = self._ship(10, "200.00")
        receive_purchase_order_item(actor=self.distributor_user, item_id=item.pk, quantity=Decimal("10"))
        sync_sales_ledger(actor=self.owner)

        self.assertEqual(SaleEntry.objects.count(), 1)

    def test_giving_stock_away_is_a_cost_with_no_revenue(self):
        give_to_distributor(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("5"),
            from_location=self.warehouse,
            distributor_profile=self.distributor,
            batch=self.batch,
        )

        entry = SaleEntry.objects.get()
        self.assertEqual(entry.kind, SaleEntry.Kind.GIFT)
        self.assertEqual(entry.net_unit_price, Decimal("0"))
        self.assertEqual(entry.retail_unit_price, Decimal("900.00"))
        self.assertEqual(entry.cost, Decimal("1150"))

    def test_shopify_sale_uses_the_price_the_customer_paid(self):
        order = ShopifyOrder.objects.create(
            shopify_order_id="1001", name="#1001", placed_at=timezone.now()
        )
        line = ShopifyOrderLine.objects.create(
            order=order,
            shopify_line_id="L1",
            title="Saw Palmetto",
            product=self.product,
            quantity=Decimal("2"),
            original_unit_price=Decimal("900.00"),
            unit_price=Decimal("810.00"),
            status=ShopifyOrderLine.Status.UNLINKED,
        )
        post_line(actor=self.owner, line=line, summary=defaultdict(int))

        entry = SaleEntry.objects.get()
        self.assertEqual(entry.kind, SaleEntry.Kind.SHOPIFY_SALE)
        self.assertEqual(entry.shopify_order_line, line)
        self.assertEqual(entry.revenue, Decimal("1620"))
        self.assertEqual(entry.cut, Decimal("180"))

    def test_period_report_profit_and_loss(self):
        self._ship(10, "400.00")
        give_to_distributor(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("1"),
            from_location=self.warehouse,
            distributor_profile=self.distributor,
            batch=self.batch,
        )
        today = timezone.localdate()
        Expense.objects.create(date=today, name="Rent", amount=Decimal("500.00"))

        report = period_report(today, today)
        totals = report.totals

        self.assertEqual(totals.units_sold, Decimal("10"))
        self.assertEqual(totals.revenue, Decimal("4000"))
        self.assertEqual(totals.cogs, Decimal("2300"))
        self.assertEqual(totals.gross_profit, Decimal("1700"))
        self.assertEqual(totals.free_cost, Decimal("230"))
        self.assertEqual(totals.expenses, Decimal("500.00"))
        self.assertEqual(totals.net_profit, Decimal("970"))
        self.assertEqual(report.stock_bought, Decimal("23000"))

    def test_product_finance_sheet_reads_batches(self):
        Expense.objects.create(name="Marketing", amount=Decimal("1000.00"))
        MarketResearch.objects.create(
            product=self.product, similar_product="Prozon", store="healthwire.pk",
            pack_size=Decimal("30"), market_price=Decimal("850.00"),
        )

        rows, totals = product_finance_sheet(cut=Decimal("60"))

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.quantity, Decimal("100"))
        self.assertEqual(row.unit_cost, Decimal("230"))
        self.assertEqual(row.retail_price, Decimal("900.00"))
        self.assertEqual(row.market_price, Decimal("1700.00"))
        self.assertEqual(row.we_receive, Decimal("360"))
        self.assertEqual(totals.investment, Decimal("23000"))
        self.assertEqual(totals.gross_profit, Decimal("13000"))
        self.assertEqual(totals.net_profit, Decimal("12000"))

    def test_sync_backfills_movements_made_before_the_ledger(self):
        self._ship(10, "200.00")
        SaleEntry.objects.all()._raw_delete(SaleEntry.objects.db)

        self.assertEqual(sync_sales_ledger(actor=self.owner), 1)
        self.assertEqual(sync_sales_ledger(actor=self.owner), 0)
        entry = SaleEntry.objects.get()
        self.assertEqual(entry.net_unit_price, Decimal("200.00"))
        self.assertIsNotNone(entry.purchase_order_item)

    def test_internal_moves_and_receipts_are_not_entries(self):
        sync_sales_ledger(actor=self.owner)
        self.assertTrue(StockMovement.objects.exists())
        self.assertFalse(SaleEntry.objects.exists())

    def test_entries_are_immutable(self):
        self._ship(1, "200.00")
        entry = SaleEntry.objects.get()

        entry.net_unit_price = Decimal("1")
        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ValidationError):
            entry.delete()

    def test_finance_pages_are_owner_only(self):
        names = [
            "owner-finance",
            "owner-finance-product-finance",
            "owner-finance-ledger",
            "owner-finance-expenses",
            "owner-finance-expense-create",
            "owner-finance-prices",
            "owner-finance-market",
            "owner-finance-market-create",
        ]
        self._ship(2, "200.00")

        owner = Client()
        owner.force_login(self.owner)
        for name in names:
            self.assertEqual(owner.get(reverse(name)).status_code, 200, name)
        for period in ("daily", "weekly", "custom"):
            self.assertEqual(owner.get(reverse("owner-finance"), {"period": period}).status_code, 200)
        csv = owner.get(reverse("owner-finance-ledger"), {"format": "csv"})
        self.assertIn(b"Distributor sale", csv.content)

        distributor = Client()
        distributor.force_login(self.distributor_user)
        for name in names:
            self.assertEqual(distributor.get(reverse(name)).status_code, 403, name)

    def test_owner_can_add_an_expense(self):
        client = Client()
        client.force_login(self.owner)

        response = client.post(
            reverse("owner-finance-expense-create"),
            {"date": "2026-10-14", "name": "E1", "category": "RENT", "amount": "670000", "comments": ""},
        )

        self.assertRedirects(response, reverse("owner-finance-expenses"))
        self.assertEqual(Expense.objects.get().amount, Decimal("670000"))

    def test_every_finance_page_downloads_as_csv_and_pdf(self):
        self._ship(50, "400.00")
        give_to_distributor(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("1"),
            from_location=self.warehouse,
            distributor_profile=self.distributor,
            batch=self.batch,
        )
        Expense.objects.create(name="Rent", amount=Decimal("500.00"))
        MarketResearch.objects.create(
            product=self.product, similar_product="Prozon", store="healthwire.pk",
            pack_size=Decimal("30"), market_price=Decimal("850.00"),
        )
        client = Client()
        client.force_login(self.owner)

        pages = [
            (reverse("owner-finance"), {"period": "daily"}),
            (reverse("owner-finance"), {"period": "weekly"}),
            (reverse("owner-finance"), {"period": "monthly"}),
            (reverse("owner-finance"), {"period": "custom", "start": "2026-01-01", "end": "2026-12-31"}),
            (reverse("owner-finance-product-finance"), {"group": "product"}),
            (reverse("owner-finance-product-finance"), {}),
            (reverse("owner-finance-ledger"), {"kind": "DISTRIBUTOR_SALE"}),
            (reverse("owner-finance-expenses"), {}),
            (reverse("owner-finance-prices"), {}),
            (reverse("owner-finance-market"), {}),
        ]
        for url, params in pages:
            csv = client.get(url, {**params, "format": "csv"})
            self.assertEqual(csv.status_code, 200, url)
            self.assertIn("attachment;", csv["Content-Disposition"])
            self.assertTrue(csv["Content-Type"].startswith("text/csv"))

            pdf = client.get(url, {**params, "format": "pdf"})
            self.assertEqual(pdf.status_code, 200, url)
            self.assertEqual(pdf["Content-Type"], "application/pdf")
            self.assertTrue(pdf.content.startswith(b"%PDF"), url)

        report = client.get(reverse("owner-finance"), {"format": "csv"}).content.decode("utf-8-sig")
        self.assertIn("Net profit", report)
        self.assertIn("By batch", report)
        self.assertIn(self.batch.batch_number, report)
        # 50 × 400 revenue, 51 × 230 cost of which 1 given free, 500 rent.
        self.assertIn("Revenue (what we actually received),20000.00", report)
        self.assertIn("Net profit,7770.00", report)

        ledger = client.get(reverse("owner-finance-ledger"), {"format": "csv"}).content.decode("utf-8-sig")
        self.assertIn(",50,", ledger)
        self.assertNotIn("E+", ledger)

        sheet = client.get(reverse("owner-finance-product-finance"), {"format": "csv"}).content.decode("utf-8-sig")
        self.assertIn("Price to make / Manufacturer price", sheet)
        self.assertIn("Totals,,23000.00", sheet)

    def test_downloads_are_owner_only(self):
        client = Client()
        client.force_login(self.distributor_user)
        for name in ("owner-finance", "owner-finance-ledger", "owner-finance-product-finance"):
            self.assertEqual(client.get(reverse(name), {"format": "csv"}).status_code, 403)

    def test_bad_filters_fall_back_instead_of_failing(self):
        client = Client()
        client.force_login(self.owner)
        self.assertEqual(
            client.get(reverse("owner-finance-ledger"), {"product": "nope", "start": "2026-02-31"}).status_code,
            200,
        )
        self.assertEqual(
            client.get(reverse("owner-finance"), {"period": "weekly", "date": "2026-13-45"}).status_code,
            200,
        )

    def test_free_and_written_off_cost_keeps_paise(self):
        from .services import Figures

        figures = Figures(free_cost=Decimal("10.40"), written_off_cost=Decimal("0.35"))
        self.assertEqual(figures.lost_cost, Decimal("10.75"))

    def test_product_prices_list_each_manufacturers_package_price(self):
        ProductPackagePrice.objects.create(
            product=self.product, manufacturer=self.manufacturer,
            amount=Decimal("60"), price=Decimal("285.00"),
        )
        other = create_manufacturer(actor=self.owner, name="Herbion Pakistan")
        ProductPackagePrice.objects.create(
            product=self.product, manufacturer=other,
            amount=Decimal("60"), price=Decimal("300.00"),
        )

        rows = [row for row in product_price_rows() if row.product == self.product]

        by_manufacturer = {row.manufacturer_name: row for row in rows}
        glorious = by_manufacturer["Glorious Laboratories"]
        self.assertEqual(glorious.manufacturer_price, Decimal("285.00"))
        self.assertEqual(glorious.latest_batch_cost, Decimal("230.0000"))
        self.assertEqual(glorious.retail_price, Decimal("900.00"))
        self.assertEqual(glorious.dropdown_name, "Saw Palmetto (60 capsules)(GL)")
        self.assertEqual(by_manufacturer["Herbion Pakistan"].manufacturer_price, Decimal("300.00"))

    def test_manufacturer_price_falls_back_to_the_latest_order(self):
        row = next(row for row in product_price_rows() if row.product == self.product)

        self.assertEqual(row.manufacturer_price, Decimal("200.000"))
        self.assertTrue(row.price_source.startswith("Order PO-"))
        self.assertEqual(row.manufacturer_name, "Glorious Laboratories")

    def test_report_filters_narrow_sales_and_leave_out_expenses(self):
        other = create_product(
            actor=self.owner, sku="FIN-OTHER", barcode="", name="Other",
            base_retail_price=Decimal("100.00"), currency="pkr",
        )
        self._ship(10, "400.00")
        give_to_distributor(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("1"),
            from_location=self.warehouse,
            distributor_profile=self.distributor,
            batch=self.batch,
        )
        today = timezone.localdate()
        Expense.objects.create(date=today, name="Rent", amount=Decimal("500.00"))

        everything = period_report(today, today)
        sales_only = period_report(today, today, kind=SaleEntry.Kind.DISTRIBUTOR_SALE)
        none = period_report(today, today, product_id=other.pk)

        self.assertEqual(everything.totals.expenses, Decimal("500.00"))
        self.assertTrue(sales_only.filtered)
        self.assertEqual(sales_only.totals.expenses, Decimal("0"))
        self.assertEqual(sales_only.totals.free_cost, Decimal("0"))
        self.assertEqual(sales_only.totals.net_profit, Decimal("1700"))
        self.assertEqual(none.entries, [])
        self.assertEqual(
            period_report(today, today, batch_number=self.batch.batch_number).totals.units_sold,
            Decimal("10"),
        )

    def test_reports_page_all_time_and_empty_period_notice(self):
        self._ship(5, "400.00")
        client = Client()
        client.force_login(self.owner)

        all_time = client.get(reverse("owner-finance"), {"period": "all"})
        self.assertEqual(all_time.context["totals"].units_sold, Decimal("5"))

        empty = client.get(reverse("owner-finance"), {"period": "monthly", "date": "2020-01-15"})
        self.assertEqual(empty.context["latest_sale"], timezone.localdate())
        self.assertContains(empty, "show that month")

        filtered = client.get(
            reverse("owner-finance"), {"period": "all", "distributor": str(self.distributor.pk)}
        )
        self.assertEqual(filtered.context["active_filters"], ["Distributor: Karachi Distributors"])
        self.assertIn("distributor=", filtered.context["filter_query"])

    def test_ledger_filters_combine_and_only_offer_what_exists(self):
        self._ship(5, "400.00")
        give_to_distributor(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("1"),
            from_location=self.warehouse,
            distributor_profile=self.distributor,
            batch=self.batch,
        )
        client = Client()
        client.force_login(self.owner)

        page = client.get(reverse("owner-finance-ledger"))
        self.assertEqual(
            [value for value, _label in page.context["kinds"]],
            [SaleEntry.Kind.DISTRIBUTOR_SALE, SaleEntry.Kind.GIFT],
        )
        self.assertEqual(page.context["batches"], [self.batch.batch_number])

        gifts = client.get(reverse("owner-finance-ledger"), {
            "kind": "GIFT",
            "distributor": str(self.distributor.pk),
            "batch": self.batch.batch_number,
        })
        self.assertEqual(gifts.context["page_obj"].paginator.count, 1)
        self.assertEqual(gifts.context["summary"].free_units, Decimal("1"))

        csv = client.get(reverse("owner-finance-ledger"), {"kind": "GIFT", "format": "csv"})
        body = csv.content.decode("utf-8-sig")
        self.assertIn("Given free to Distributor", body)
        self.assertNotIn("Distributor sale", body)

    def test_empty_columns_are_left_out(self):
        self._ship(5, "400.00")
        client = Client()
        client.force_login(self.owner)

        prices = client.get(reverse("owner-finance-prices"))
        self.assertFalse(prices.context["columns"]["market"])
        self.assertNotContains(prices, "Retail vs market</th>")

        report = client.get(reverse("owner-finance"), {"period": "all", "format": "csv"})
        body = report.content.decode("utf-8-sig")
        self.assertNotIn("Free / written off (cost)", body)
        self.assertNotIn("Expenses,Net profit", body)
        self.assertIn("Distributor cut / discount", body)
