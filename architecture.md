# Distribution & Finance Platform — System Architecture

**Scope:** Licence-holder / brand-owner / distributor operating in Pakistan. Finished goods bought from a contract manufacturer, relabelled under own enlistment, sold through two channels (Shopify online + physical distribution to distributors, pharmacies, retailers).

**Not in scope:** Manufacturing. Production lives in the separate Saskatoon system.

---

## 1. Foundational principles

These are non-negotiable. Every design decision below follows from them.

1. **Balances are never stored as editable fields.** Quantity on hand, distributor outstanding, partner capital — all are derived from immutable ledgers. Cached for speed, never authored.
2. **Stock movement is double-entry.** Every movement has a `from_location` and a `to_location`, always. Receipts come from a supplier pseudo-location; sales go to a customer pseudo-location. Nothing appears or disappears — it moves. The sum of all movements across all locations is always zero.
3. **Ownership is a property of location, not a separate module.** `Location.on_book` decides whether stock is your asset.
4. **One write path for stock.** A single service function posts movements inside one database transaction with row locks. No model saves anywhere else touch balances.
5. **Reservations are not movements.** Ordered ≠ shipped. Reservations hold availability; only physical dispatch writes a movement.
6. **Closed periods are immutable.** Corrections after close are dated adjustments in the current period, linked to what they correct.
7. **Money is `Decimal`. Never float. Ever.**

---

## 2. Technology stack

| Layer | Choice | Reason |
|---|---|---|
| Runtime | Python 3.12 | — |
| Framework | Django 5.x | Admin, ORM, migrations, auth out of the box |
| Database | PostgreSQL 16 | Row locking, partial indexes, materialised views |
| API | Django REST Framework | Mobile app + Shopify sync later |
| UI | HTMX + Alpine.js + Tailwind | Server-rendered, fast to build, restyle without rewrite |
| Async | Celery + Redis | Shopify sync, alerts, snapshots, reports |
| Audit | `django-simple-history` on master data | Movements are already immutable |
| Files | S3-compatible object store | COAs, invoices, courier remittance files |
| Monitoring | Sentry + structured logs | — |
| Deploy | Docker Compose → gunicorn + nginx | — |

**Why HTMX over React:** you asked for a basic UI with correct UX on day one, restyled later. HTMX gives you responsive server-rendered pages in a fraction of the time, and the DRF API is still there when you want a mobile app. A React SPA would double the build for zero day-one benefit.

---

## 3. Django project layout

```
config/                 settings, celery, urls, wsgi
apps/
  core/                 Company, Partner, FiscalPeriod, numbering, base models
  accounts/             User, Django groups, row scoping, cost-field masking
  parties/              Manufacturer, Distributor, Retailer, Courier
  catalog/              Product, Ingredient, ProductIngredient
  warehouse/            Location, LocationType, capacity
  inventory/            Batch, StockMovement, StockBalance, Reservation,
                        StockCount, PeriodSnapshot  ← the heart
  procurement/          PO, GoodsReceipt, LandedCost, SupplierInvoice, Payment
  sales/                Agreement, StockRequest, Order, Dispatch, Invoice,
                        Receipt, CreditNote, SalesReturn, SellThroughReport
  channels_shopify/     Store, VariantMap, WebhookEvent, SyncRun, Publisher
  logistics/            Shipment, Courier, CODRemittance, RemittanceLine
  finance/              ExpenseCategory, Expense, CostAllocation, PeriodClose,
                        ProfitDeclaration, PartnerAllocation, CapitalEntry
  notifications/        AlertRule, Alert, DeliveryLog
  reporting/            read-only views, exports, dashboards
```

Each app exposes a `services.py`. Views and API endpoints call services. Services own transactions. Models never contain business logic that spans tables.

---

## 4. Data model

### 4.1 core

**Company** — `name`, `legal_name`, `enlistment_no`, `base_currency` (PKR), `fiscal_year_start`

**Partner** — `name`, `user` (nullable), `share_pct`, `active_from`, `active_to`
Historical shares matter. A period's split uses the shares in effect during that period, not today's.

**FiscalPeriod** — `year`, `month`, `status` (`OPEN` / `CLOSED` / `LOCKED`), `closed_by`, `closed_at`
Every movement and financial entry carries a period FK. Writing into a non-`OPEN` period raises.

**DocumentSequence** — `doc_type`, `prefix`, `year`, `next_number`
Locked increment for human-readable numbers: `GRN-2026-0043`, `DSP-2026-0871`, `INV-2026-1204`.

### 4.2 parties

**Party** — `code`, `name`, `party_type` (`MANUFACTURER`, `DISTRIBUTOR`, `RETAILER`, `COURIER`, `CUSTOMER`), `ntn`, `strn`, `contacts`, `addresses`, `territory`, `active`

One table, typed. A party can hold multiple types (a distributor who also buys direct).

### 4.3 catalog

**Product** — `sku`, `name`, `generic_name`, `indications`, `enlistment_no`, `form`, `pack_size`, `uom`, `serving_size`, `strength_basis` (`PER_CAPSULE` / `PER_SERVING`), `unit_price`, `currency`, `shelf_life_months`, `hs_code`, `reorder_point`, `reorder_qty`, `active`

One price on the product. No price lists. Distributor pricing derives from `unit_price` minus that distributor's `trade_discount_pct`.

**Every document line snapshots the price it used.** `OrderLine`, `DispatchLine` and `InvoiceLine` each carry their own `unit_price` copied at creation, never a live lookup to the product. Without this, raising a price silently rewrites every historical invoice and every past margin.

**Ingredient** — `name`, `botanical_name`, `part_used`, `default_unit`
**ProductIngredient** — `product`, `ingredient`, `strength`, `unit` (`MG` / `MCG` / `G` / `IU` / `ML` / `PCT`), `is_medicinal`, `extract_ratio`, `equivalent_to`, `sort_order`

Entered on the product form alongside the price. `is_medicinal` separates medicinal from non-medicinal ingredients for the DRAP declaration. `extract_ratio` and `equivalent_to` cover herbal extracts — a 4:1 extract at 250 mg equivalent to 1000 mg dried herb — and stay blank for straight vitamins and minerals.

### 4.4 warehouse

**Location** — the keystone table.

| Field | Notes |
|---|---|
| `code`, `name` | — |
| `location_type` | `OWN`, `IN_TRANSIT`, `CONSIGNMENT`, `SHOPIFY_POOL`, `QUARANTINE`, `DAMAGED`, `SUPPLIER`, `CUSTOMER` |
| `party` | Set for `CONSIGNMENT`, `CUSTOMER`, `SUPPLIER` |
| `on_book` | Counts toward your inventory asset |
| `is_physical` | False for `SUPPLIER` / `CUSTOMER` pseudo-locations |
| `is_sellable` | Can fulfil orders from here |
| `address`, `area_sqft`, `capacity_units` | **Advisory only** |

**Capacity never blocks a receipt.** It drives a utilisation percentage and a warning. Things go over the book; the software must not refuse reality.

`on_book` truth table:

| Type | on_book | physical |
|---|---|---|
| `OWN` | yes | yes |
| `IN_TRANSIT` | yes | yes |
| `CONSIGNMENT` | yes | yes |
| `SHOPIFY_POOL` | yes | earmarked (stock sits in `OWN`, allocated for online sale) |
| `QUARANTINE` / `DAMAGED` | yes | yes |
| `SUPPLIER` / `CUSTOMER` | no | no |

### 4.5 inventory — the heart of the system

**Batch**
`product`, `batch_code`, `manufacturer` (party), `mfg_date`, `expiry_date`, `source_doc_type`, `source_doc_id`, `unit_cost` (landed, computed), `currency`, `status` (`QUARANTINE` / `RELEASED` / `HOLD` / `EXPIRED` / `RECALLED`), `coa_file`

Unique on `(product, batch_code)`. Landed cost lives here — this is what makes COGS honest.

**StockMovement** — append-only. No `UPDATE`, no `DELETE`, enforced by database trigger.

| Field | Notes |
|---|---|
| `movement_no` | `MOV-2026-004312` |
| `movement_type` | `RECEIPT`, `DISPATCH`, `TRANSFER`, `SALES_RETURN`, `PURCHASE_RETURN`, `ADJUSTMENT`, `DAMAGE`, `EXPIRY_WRITE_OFF`, `CONSIGNMENT_OUT`, `SELL_THROUGH`, `COUNT_CORRECTION` |
| `product`, `batch` | Batch mandatory on every physical movement |
| `from_location`, `to_location` | Both always set |
| `quantity` | Always positive. Direction comes from the locations. |
| `occurred_at`, `posted_at`, `fiscal_period` | — |
| `channel` | `SHOPIFY`, `DISTRIBUTOR`, `DIRECT`, `INTERNAL` |
| `source_doc_type`, `source_doc_id` | Dispatch note, GRN, count, remittance |
| `unit_cost` | Snapshotted from batch at posting time |
| `created_by`, `reason` | — |
| `reversal_of` | Self FK. Corrections reverse, never edit. |

Indexes: `(product, batch, from_location)`, `(product, batch, to_location)`, `(fiscal_period, channel)`, `(occurred_at)`, `(source_doc_type, source_doc_id)`.

**StockBalance** — derived cache, unique on `(product, batch, location)`
`qty_on_hand`, `last_movement`, `updated_at`

Updated inside the same transaction as the movement, under `SELECT ... FOR UPDATE`. A management command rebuilds it from movements and must always reconcile to zero difference. That rebuild is your correctness test.

**Reservation**
`product`, `location`, `quantity`, `source_doc_type`, `source_doc_id`, `status` (`ACTIVE` / `RELEASED` / `CONSUMED`), `expires_at`, `created_at`

Reservations are at product + location level, **not batch** — because the batch is chosen at pick time. Released reservations leave no ledger trace, which is exactly what you want for cancellations.

```
available(product, location) = Σ StockBalance.qty_on_hand
                             − Σ Reservation.quantity WHERE status = ACTIVE
```

**StockCount** / **StockCountLine** — physical counts, especially at consignment locations. Variance posts as `COUNT_CORRECTION` movements. Never edits a balance.

**PeriodSnapshot** — written at period close
`fiscal_period`, `product`, `batch`, `location`, `closing_qty`, `closing_value`

Plus **PeriodChannelSummary**: `fiscal_period`, `channel`, `product`, `qty_out`, `revenue`, `cogs`. This is your "what went out through Shopify vs physical this month" report, pre-aggregated.

### 4.6 procurement

`PurchaseOrder` → `POLine` → `GoodsReceipt` → `GRNLine` → `SupplierInvoice` → `SupplierPayment`

**LandedCostAllocation** — `goods_receipt`, `cost_type` (`FREIGHT`, `LABELLING`, `TESTING`, `DUTY`, `CLEARING`, `OTHER`), `amount`, `allocation_basis` (`VALUE` / `QTY` / `WEIGHT`)

On GRN posting: batches are created, landed costs allocated across lines, `Batch.unit_cost` computed, and `RECEIPT` movements posted from the supplier pseudo-location into `QUARANTINE` (or straight to `OWN` if you skip incoming QC).

### 4.7 sales

**DistributorAgreement** — `party`, `terms` (`SELL_IN` / `CONSIGNMENT`), `trade_discount_pct`, `commission_pct`, `credit_limit`, `credit_days`, `territory`, `active_from`, `active_to`
**AgreementLine** — per-product override of terms, discount, or commission.

A distributor can be sell-in on some products and consignment on others. Terms resolve: order line → agreement line → agreement default.

**StockRequest** → **SalesOrder** → **Dispatch** → **Invoice** → **Receipt**

`StockRequest` is the distributor's indent ("this pharmacy wants 200"). It carries `requested_qty` and `approved_qty` separately — you rarely ship exactly what was asked.

`DispatchLine` carries `batch` explicitly. This is the manual batch selection you asked for. The picker screen sorts available batches by expiry, pre-selects the earliest, warns on expired or sub-90-day stock, and allows override.

**SellThroughReport** / lines — consignment only. The distributor reports what they sold; this posts `SELL_THROUGH` movements out of their consignment location to the customer pseudo-location and generates the invoice.

**SalesReturn** and **CreditNote** — first-class documents. Near-expiry returns from pharmacies are routine; if they're modelled as adjustments your stock and margins drift.

### 4.8 channels_shopify

**ShopifyStore** — `shop_domain`, `access_token` (encrypted), `pool_location` (FK to the `SHOPIFY_POOL` location)
**VariantMap** — `product` ↔ `shopify_variant_id`, `shopify_inventory_item_id`, `shopify_location_id`
**WebhookEvent** — `topic`, `shopify_event_id`, `raw_payload` (JSONB), `hmac_verified`, `received_at`, `processed_at`, `attempts`, `error`

Unique on `(topic, shopify_event_id)` — this is your idempotency guarantee. Shopify redelivers; you must not double-process.

**ShopifyOrderMirror** — local copy of order header, financial status, fulfillment status, gateway and channel fees, plus links to the reservation and eventual dispatch.

### 4.9 logistics

**Shipment** — `courier`, `tracking_no`, `dispatch`, `payment_mode` (`PREPAID` / `COD`), `cod_amount`, `status` (`BOOKED` → `IN_TRANSIT` → `DELIVERED` → `CASH_COLLECTED` → `REMITTED`, or → `RTO`)
**CODRemittance** / **RemittanceLine** — imported from the courier's payout file, matched to shipments, difference posted as a courier-charge expense.

RTO shipments post a return movement back into `OWN` and release the revenue.

### 4.10 finance

**ExpenseCategory** — tree. Seed: Marketing, Salaries, Rent, Utilities, Freight (outbound), Courier charges, Regulatory & licence, Packaging & labels, Distributor commission, Bank & gateway fees, Professional fees, Travel, Depreciation, Other.

**Expense** — `category`, `fiscal_period`, `amount`, `currency`, `party`, `cost_center`, `allocation_basis`, `attachment`
Optionally allocated to product or channel; unallocated expenses stay in operating overhead.

**ProfitDeclaration** — `fiscal_period`, `gross_profit`, `operating_expenses`, `net_profit`, `distributable_amount`, `status` (`DRAFT` / `APPROVED`), `approved_by`

**PartnerAllocation** — `declaration`, `partner`, `share_pct`, `amount`, then split into `paid_out`, `to_reserve`, `to_reinvestment`. The three must sum to `amount`.

**CapitalEntry** — per-partner ledger: `partner`, `entry_type` (`CONTRIBUTION`, `ALLOCATION`, `WITHDRAWAL`, `RESERVE_TRANSFER`), `amount`, `date`, `reference`
A partner's balance is the sum of their entries. Not a field.

---

## 5. How the numbers compute

### Revenue recognition — by channel

| Channel | Recognised at |
|---|---|
| Sell-in distributor | Dispatch (title transfers, receivable created) |
| Consignment distributor | Sell-through report |
| Shopify prepaid | Fulfillment |
| Shopify COD | Cash collected from courier |
| Direct / walk-in | Invoice |

### Per-line contribution margin

```
net_revenue      = unit_price × qty − trade_discount − returns
cogs             = qty × batch.unit_cost
commission       = consignment only: net_revenue × commission_pct
channel_fees     = Shopify + gateway fees (from the order payload)
freight_out      = courier charge, actual where known
─────────────────────────────────────────────────────
contribution     = net_revenue − cogs − commission − channel_fees − freight_out
```

**The trap to avoid:** on sell-in, the distributor's margin is a *discount*, not an expense. Your revenue is what they paid you. On consignment, the cut *is* a commission expense against the full retail value. Two different fields, two different treatments. Model them the same way and every sell-in order double-counts.

### Period P&L

```
gross_profit  = Σ contribution across all channels
net_profit    = gross_profit − unallocated operating expenses
distributable = net_profit − reserve policy − tax provision
```

Then per partner: `distributable × share_pct` → split across paid out / reserve / reinvestment → three `CapitalEntry` rows.

---

## 6. Access control

Three roles, built on Django's built-in groups and permissions. No custom RBAC framework — the user count does not justify one.

| Role | Who | Grants |
|---|---|---|
| **Owner** | You and any co-partners | Everything: inventory, sales, costs, P&L, partner ledger, period close |
| **Staff** | One employee | Inventory operations — receipts, dispatches, transfers, counts, stock requests. **No costs, no prices, no margins, no finance.** |
| **Distributor** | One or two close distributors | Own stock requests, own invoices, own outstanding balance, own consignment stock. Nothing else. |

Adding a role later (an accountant, a second employee) is a new group plus a few permission grants, not a rewrite.

### Two enforcement layers

**Field-level masking is the one that matters most.** The risk with the Staff role is not the finance menu — it is `unit_cost` sitting on the batch record that appears during a goods receipt. That number is your manufacturer price, and it is the most sensitive figure in the business. So `unit_cost`, `landed_cost`, `margin` and `commission_pct` are stripped at the serializer and template layer unless the user holds `finance.view_cost` — everywhere they appear, including inventory screens, batch detail pages, and CSV exports.

**Row-level scoping.** Every manager exposes `for_user(user)`. A distributor's queryset is filtered to `party=user.party` at the ORM level, never in a template. Enforced in a base viewset mixin so a forgotten filter fails closed.

Keep this strict even though the distributors are close relations. If one of them can see that another gets a better trade price, you have created a commercial problem that has nothing to do with software. The filter costs nothing to have right from the start.

### Audit log

Keep it even with four users. Not for policing anyone — for answering "when was this entered, and by whom" when a number looks strange six months from now. `django-simple-history` on master data; movements are already immutable and carry `created_by`.

---

## 7. Shopify integration

**Model: Django owns physical truth. Shopify owns the online sellable pool.**

Sync is **manual push, automatic pull** — no webhooks in v1. Two operations moving in opposite directions.

| | Direction | Trigger | Effect |
|---|---|---|---|
| **Push** | App → Shopify | Owner presses Update | Sets Shopify available qty from computed on-hand |
| **Pull** | Shopify → App | Login (Owner/Staff), manual button, 30-min job | Fetches orders since last sync so the ledger catches up |

When Shopify sells, Shopify decrements its own number. Nothing needs pushing at that moment — the app is the side that's stale, and pull is what fixes it.

### Changing the online allocation

`SHOPIFY_POOL` is a real ledger location, not a computed view. Deciding how much to offer online is a `TRANSFER` movement, in either direction:

```
"list 100 online"        TRANSFER  OWN → SHOPIFY_POOL   100
"pull 40 back"           TRANSFER  SHOPIFY_POOL → OWN    40   reason: needed for Karachi order
```

Total on-hand is unchanged by either — the stock never leaves the warehouse, only its allocation changes. The push then publishes `pool balance − active reservations`.

Recording these as transfers with a reason means a batch's history explains itself later. An unexplained drop in the online number is the thing you do not want to be looking at six months from now.

### Push — always pull first

The Update button is one action, three steps, in this order:

```
1. pull orders since last_synced_at
2. recompute available(product, SHOPIFY_POOL)
3. show diff → on confirm, inventoryLevels.set
```

**Pushing without pulling first is the oversell bug.** Compute 60 at 10:00, Shopify sells 8 at 10:40, push the stale 60 at 10:45 — Shopify now advertises 8 units that do not exist.

The diff is shown before commit, not after:

```
SKU-A   Shopify: 45  →  60   (+15 received Tue)
SKU-B   Shopify: 12  →  12   (no change)
SKU-C   Shopify:  8  →   0   (batch expired)
```

### Pull — order lifecycle

```
pull run           → GET orders?updated_at_min=last_synced_at
                   → upsert ShopifyOrderMirror keyed on shopify_order_id
                   → create ACTIVE Reservation. No movement yet.
staff assigns batch, confirms dispatch
                   → post DISPATCH movement (OWN → CUSTOMER)
                   → consume reservation
cancelled order    → release reservation. No ledger trace.
refunded order     → return movement into OWN, or credit note if not returned
```

Waiting until dispatch to post the movement is what keeps cancellations and COD returns out of the ledger. A pulled order lands in a **needs-fulfillment queue**, because batch selection is manual by design.

### Sync safety

- **Non-blocking on login.** Fires after page load with a status banner. Shopify being slow or down must never prevent login.
- **Advisory lock**, one sync at a time. Two users logging in together must not double-process.
- **Idempotent by `shopify_order_id`.** Even a double run is a no-op on the second pass.
- **`last_synced_at` cursor** per store, advanced only on successful completion.
- **30-minute background job** as a safety net. Login-only sync means a quiet weekend leaves days of orders invisible and the next push built on a stale number.
- **Owner and Staff only.** A distributor login triggers nothing.

### Reconciliation

Nightly job compares Shopify's published availability against computed availability per SKU. Drift is reported, never auto-corrected — drift means a bug, and silently fixing it hides the bug.

**Deferred to v2:** webhook subscriptions (`orders/create`, `orders/cancelled`, `refunds/create`) to replace polling. The pull-based design above stays as the fallback path regardless, since webhooks get missed.

---

## 8. Background jobs

| Job | Schedule | Purpose |
|---|---|---|
| `pull_shopify_orders` | On login (Owner/Staff), manual, every 30 min | Fetch orders since cursor |
| `push_shopify_availability` | Manual button only | Pull → recompute → diff → set |
| `reconcile_shopify` | Nightly | Drift detection |
| `check_reorder_points` | Daily | Below-threshold alerts |
| `check_expiry` | Daily | 90 / 60 / 30-day warnings, auto-flag expired |
| `check_receivables` | Daily | Overdue and over-limit distributors |
| `expire_stale_reservations` | Hourly | Release abandoned carts |
| `close_period` | Manual, month end | Snapshot + lock |
| `rebuild_balances` | On demand | Correctness verification |

---

## 9. Alerts

**AlertRule** — `rule_type`, `product` (nullable), `location` (nullable), `threshold`, `channels`, `recipients`, `active`

Thresholds live in the database, editable per product and per warehouse. Never hardcoded. Your "tell us when it's below 100" becomes a row, and each product can have its own number.

Channels: in-app, email, WhatsApp. WhatsApp matters more than email for warehouse and distributor staff in Pakistan.

---

## 10. UX requirements for day one

The UI will be restyled. These behaviours must not be.

- **Every number is drillable.** Click any quantity or balance and land on the movements that produced it. This single feature removes most "why is this wrong" conversations.
- **Every quantity shows its as-of timestamp.** A number without a time is a rumour.
- **Keyboard-first entry** on dispatch, receipt and count screens. Tab order follows the physical task. Inputs accept barcode scanner input (scan = keystrokes + Enter).
- **Phone-first for warehouse screens.** Dispatch and counting happen standing up holding a phone, not at a desk.
- **Soft warnings, hard blocks rarely.** Hard-block only on: negative stock, posting into a closed period, and dispatch to an over-limit or overdue distributor. Everything else warns and proceeds.
- **Forms survive failure.** Poor connectivity is normal. A failed submit must not lose entered data.
- **Saved filters on every list.** "My territory, overdue, this month" is one click, not five.
- **Confirmation shows consequences.** "Dispatch 200 units of batch B-2451, expiring in 84 days, to Karachi Distributors — outstanding after this: PKR 1,240,000 of a 1,500,000 limit."

---

## 11. Build sequence

| Phase | Contents | Rough effort |
|---|---|---|
| 0 | Project skeleton, users, roles, parties, products, price lists, locations | 2–3 weeks |
| 1 | **Inventory core** — batches, movements, balances, reservations, receipts, dispatches, transfers, counts, alerts | 4–5 weeks |
| 2 | Sales — agreements, requests, orders, dispatches, invoices, receipts, returns, sell-through, receivables aging | 4 weeks |
| 3 | Shopify sync + COD / courier reconciliation | 3 weeks |
| 4 | Finance — landed cost, expenses, contribution margin, period close, P&L, partner distribution | 4 weeks |
| 5 | Dashboards, distributor portal, exports | 3 weeks |

**Do not start Phase 4 until warehouse staff trust Phase 1.** Financial reports are only as good as the stock ledger under them. If the quantities are wrong, the P&L is confidently wrong, which is worse than having no P&L.

Phase 1 is the one to over-engineer. Everything else depends on it and it is the hardest to change later.

---

## 12. Deliberately deferred

- Multi-company / multi-entity consolidation — schema carries `company_id` from day one, but no cross-entity reporting
- Multi-currency beyond PKR + CAD purchase costs
- Bin and rack-level locations inside a warehouse
- Automated FEFO picking (manual selection by design, per your call)
- Integration with the Saskatoon manufacturing system — export/import format only
- Native mobile app (DRF API exists; PWA first)

## 13. Open items to confirm before Phase 2

- **Tax model.** Sales tax rates, withholding on distributor payments, and current FBR digital invoicing obligations for your category. Confirm with your accountant. Invoices carry a tax-lines table from day one even if it stays empty — retrofitting tax is painful.
- **Free goods / bonus schemes.** "13+1" style trade offers are standard. These need modelling as zero-revenue lines that still consume stock and carry COGS, or your margins will read better than reality.
- **Opening balances.** How existing stock, existing distributor balances, and existing partner capital get loaded on day one.
