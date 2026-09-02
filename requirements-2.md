# Distribution & Finance Platform
## Ten-Stage Software Requirements Specification

**Document type:** Product requirements and implementation specification  
**Backend:** Django modular monolith  
**Primary market:** Pakistan  
**Primary currency:** PKR  
**System users:** Owner and Distributor only  
**Version:** 1.0  
**Status:** Approved architecture baseline for staged implementation

---

## 1. Product Scope

The platform will manage the distribution and financial operations of a licence-holder or brand owner that:

- Purchases finished products from one or more contract manufacturers.
- Stores finished products in owned, quarantine, in-transit, consignment, damaged-hold and expired-hold locations.
- Sells products through Shopify, direct/physical sales and distributors.
- Supports both sell-in and consignment distributor arrangements.
- Tracks products by SKU, batch, manufacture date, expiry date and landed unit cost.
- Tracks purchasing, stock, reservations, dispatches, invoices, payments, returns, expenses and operational profit.
- Calculates distributor outstanding balances from immutable financial entries.
- Calculates inventory balances from immutable double-entry stock movements.
- Provides a restricted distributor portal and a complete owner portal.

---

## 2. Authoritative User Roles

Only two authenticated roles are allowed:

### 2.1 Owner

The Owner can:

- Configure the company and system.
- Create and manage products, parties, prices and locations.
- Record purchases and goods receipts.
- Release or hold batches.
- Create stock movements, transfers, counts and adjustments.
- Manage all distributors and agreements.
- Approve, reject, dispatch, invoice and return orders.
- View product costs, landed costs, commissions and margins.
- Manage Shopify integration.
- Record payments and expenses.
- Close fiscal periods.
- View all dashboards and reports.
- Manage other Owner accounts if authorized.

### 2.2 Distributor

The Distributor can:

- View only products available to that distributor.
- View only the distributor price or discount applicable to their agreement.
- Create and view their own stock requests and orders.
- View their own dispatches, invoices, credit notes and payments.
- View their own outstanding balance and credit availability.
- View their own consignment stock, if consignment is enabled.
- Submit sell-through reports for their own consignment stock.
- Submit return requests.
- Download their own approved documents.
- Update permitted contact and delivery information.

The Distributor cannot:

- See product purchase cost, batch landed cost, total company stock or company profit.
- See suppliers, manufacturers, equity partners or other distributors.
- See another distributor's prices, discounts, commissions, limits or transactions.
- Post inventory movements, approve orders, create invoices or close periods.
- Access Shopify configuration or synchronization.

### 2.3 Non-user entities

Manufacturers, suppliers, retailers, couriers, customers and equity partners are business records. They are not authenticated users unless the scope is formally changed.

---

## 3. Foundational Architecture Rules

These requirements apply to all ten stages:

1. **Modular monolith:** One Django project divided into bounded Django applications. Microservices are not permitted in version one.
2. **Company scoping:** Every transactional and master-data record must belong to a company, even if only one company is active initially.
3. **Immutable stock ledger:** Stock movements cannot be edited or deleted after posting.
4. **Double-entry stock movement:** Every stock movement must have a source location and destination location. Quantity is positive; direction is determined by the two locations.
5. **One stock write path:** All stock changes must pass through one inventory posting service inside a database transaction.
6. **Derived balances:** Stock on hand, distributor outstanding balance and equity-partner capital balance are derived from immutable entries. Cached balances may exist for speed but cannot be manually authored.
7. **Reservations are not movements:** An approved or imported order reserves availability. Physical dispatch creates the stock movement.
8. **Batch-level physical inventory:** Every physical receipt, dispatch, transfer, return, write-off and count correction must reference a batch.
9. **Historical snapshots:** Prices, discounts, tax amounts, unit costs and commission rates used by a posted document must be saved on the document line.
10. **Closed periods are immutable:** New transactions cannot be posted into closed or locked periods.
11. **Corrections use reversals:** Posted records are corrected through reversal entries and replacement documents, not editing.
12. **Decimal money:** Money and percentages must use decimal arithmetic. Floating-point values are prohibited.
13. **Fail-closed access:** If distributor ownership cannot be proven, access must be denied.
14. **Idempotency:** External events and posting actions must be safe to retry without creating duplicate transactions.
15. **Auditability:** Every important master-data change and every posting action must identify the user, timestamp and source document.
16. **Server-side enforcement:** UI restrictions are not security. Permissions, company scope and distributor scope must be enforced in services and querysets.
17. **No negative sellable stock:** A dispatch cannot make sellable stock negative unless an approved backorder feature is added later.
18. **Soft warnings, limited hard blocks:** Warnings may be overridden with a reason. Negative stock, closed periods, invalid permissions and invalid distributor credit are hard blocks.

---

## 4. Technology and Delivery Standards

| Layer | Requirement |
|---|---|
| Runtime | Python 3.12 |
| Framework | Latest supported patch of Django 5.2 LTS |
| API | Django REST Framework |
| Database | PostgreSQL 16 or later supported release |
| Web UI | Django templates, HTMX, Alpine.js and Tailwind CSS |
| Background work | Celery with Redis |
| Files | S3-compatible object storage |
| App server | Gunicorn |
| Reverse proxy | Nginx |
| Packaging | Docker and Docker Compose |
| Monitoring | Structured logs and Sentry-compatible error tracking |
| Tests | pytest, pytest-django and factory-based fixtures |
| API format | JSON over HTTPS under `/api/v1/` |

### 4.1 Source layout

~~~text
config/
  settings/
    base.py
    local.py
    test.py
    production.py
  urls.py
  celery.py
  wsgi.py

apps/
  core/
  accounts/
  parties/
  catalog/
  warehouse/
  inventory/
  procurement/
  sales/
  channels_shopify/
  logistics/
  finance/
  notifications/
  reporting/
  audit/

templates/
static/
media/
tests/
manage.py
~~~

Each business application must expose a `services/` package or `services.py`. Views, serializers, commands and Celery tasks call services. Cross-table business logic must not be hidden in model `save()` methods or signals.

---

## 5. Common Data Standards

### 5.1 Primary keys and timestamps

- Use UUID primary keys for business records.
- Use timezone-aware `created_at` and `updated_at`.
- Use `created_by` and `updated_by` where appropriate.
- Store timestamps in UTC and display them in the configured company timezone.
- Default company timezone: `Asia/Karachi`.

### 5.2 Money and quantities

- Money: `DecimalField(max_digits=18, decimal_places=2)` unless currency requires another scale.
- Unit cost: at least four decimal places.
- Percentages: at least four decimal places internally.
- Inventory quantity: decimal-capable so future units are supported; enforce whole numbers for products whose UOM is `PIECE`, `BOTTLE`, `BOX` or `CASE`.
- Every money field must have a currency.

### 5.3 Status transitions

- Status changes must happen through named service methods.
- Posted, closed, cancelled or reversed documents must not return to editable draft status.
- Every rejected, cancelled, adjusted or overridden action requires a reason.

### 5.4 Deletion

- Posted transactions cannot be deleted.
- Referenced master data cannot be hard-deleted.
- Master data uses `active=False` for retirement.
- Draft records may be deleted only when they have no posted dependencies.

### 5.5 Numbering

Human-readable document numbers must be generated using locked sequences:

~~~text
PO-2026-000001
GRN-2026-000001
MOV-2026-000001
TRF-2026-000001
REQ-2026-000001
ORD-2026-000001
DSP-2026-000001
INV-2026-000001
RCPT-2026-000001
CRN-2026-000001
RET-2026-000001
EXP-2026-000001
~~~

Numbers must never be reused after cancellation.

---

# Stage 1 — Project Foundation and Engineering Baseline

## 1.1 Objective

Create a stable Django project, development environment and deployment baseline on which all later stages depend. This stage contains no operational stock or finance workflows.

## 1.2 Django applications

- `core`
- `audit`
- Initial configuration for all future apps

## 1.3 Functional requirements

1. Create the Django project and application structure.
2. Configure separate local, test and production settings.
3. Load secrets exclusively from environment variables.
4. Configure PostgreSQL as the application database.
5. Configure Redis and Celery.
6. Configure S3-compatible file storage for production.
7. Implement a `/health/` endpoint that reports application and database availability without leaking secrets.
8. Add structured application logging with request and correlation IDs.
9. Configure centralized exception handling for web and API requests.
10. Configure static files, uploaded files, email backend and allowed hosts.
11. Add Dockerfiles and Docker Compose services for Django, PostgreSQL, Redis and Celery.
12. Add CI commands for linting, migrations, system checks and tests.

## 1.4 Core abstract models

Implement:

### `UUIDModel`

- `id`

### `TimeStampedModel`

- `created_at`
- `updated_at`

### `UserStampedModel`

- `created_by`
- `updated_by`

### `ActiveModel`

- `active`
- `deactivated_at`
- `deactivated_by`

These models must not assume a concrete user model before Stage 2 migrations are ready.

## 1.5 Audit foundation

Create an `AuditEvent` model:

- `company` nullable until Stage 2
- `actor`
- `action`
- `object_type`
- `object_id`
- `object_label`
- `before_data` JSON
- `after_data` JSON
- `reason`
- `request_id`
- `ip_address`
- `user_agent`
- `created_at`

Sensitive values such as passwords, access tokens and secret keys must never be written to audit JSON.

## 1.6 Required endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health/` | Basic liveness |
| GET | `/health/ready/` | Database and Redis readiness |
| GET | `/api/v1/meta/` | API name and compatible version |

## 1.7 Non-functional requirements

- Production debug mode must be disabled.
- Startup must fail when critical production environment variables are absent.
- The same migrations must run in local, test and production environments.
- Application logs must not contain passwords, Shopify tokens or personal financial data.
- All dependencies must be version-pinned through a lockable dependency file.

## 1.8 Tests

- Settings import tests for local, test and production.
- Database connection test.
- Redis/Celery configuration test.
- Health endpoint tests.
- Exception-response format tests.
- Audit redaction tests.
- Docker build validation.

## 1.9 Definition of Done

Stage 1 is complete when:

- The project starts through Docker Compose.
- PostgreSQL migrations execute from an empty database.
- Health endpoints pass.
- A Celery test task executes.
- Automated tests run in CI.
- Production settings pass Django deployment checks.
- No operational models have been implemented prematurely.

---

# Stage 2 — Company, Identity, Roles and Period Control

## 2.1 Objective

Implement the company boundary, custom authentication, the two authorized roles, fiscal periods, document sequences and foundational row scoping.

## 2.2 Django applications

- `core`
- `accounts`
- `audit`

## 2.3 Models

### `Company`

- `id`
- `name`
- `legal_name`
- `enlistment_number`
- `ntn`
- `strn`
- `base_currency` default `PKR`
- `timezone` default `Asia/Karachi`
- `fiscal_year_start_month`
- `default_country` default `PK`
- `default_low_stock_threshold`
- `logo`
- `address`
- `phone`
- `email`
- `active`

### `User`

Custom model extending `AbstractUser`:

- `id`
- `role`: `OWNER` or `DISTRIBUTOR`
- `company`
- `phone`
- `must_change_password`
- `last_password_changed_at`
- standard Django `is_active`
- standard Django authentication fields

The `party` foreign key is added to `User` during Stage 3, after the `Party` model exists. It is nullable for Owner and mandatory for an approved Distributor.

### `EquityPartner`

- `company`
- `name`
- `share_percentage`
- `active_from`
- `active_to`
- `active`

Equity partners are not automatically users.

### `FiscalPeriod`

- `company`
- `year`
- `month`
- `start_date`
- `end_date`
- `status`: `OPEN`, `CLOSED`, `LOCKED`
- `closed_by`
- `closed_at`
- `locked_by`
- `locked_at`
- `notes`

Unique on company, year and month.

### `DocumentSequence`

- `company`
- `document_type`
- `prefix`
- `year`
- `next_number`
- `padding`

Unique on company, document type and year. Increment under a database row lock.

## 2.4 Authentication requirements

- Login with username/email and password.
- Secure logout.
- Password reset through configured email.
- Owner-created distributor invitations.
- Distributor account remains inactive until explicitly approved.
- Owner can deactivate a distributor immediately.
- Deactivated users cannot obtain new sessions or API tokens.
- Existing sessions for a deactivated user must be invalidated.
- Password policy must be configurable.
- Admin superuser creation must not bypass company-scoping requirements in business views.

## 2.5 Authorization requirements

- Create Django Groups named `Owner` and `Distributor`.
- `User.role` is the authoritative high-level role.
- Groups and permissions control detailed capabilities.
- Owner has all company operational permissions.
- Distributor permissions are restricted to distributor portal actions.
- Distributor access must require:
  - Authenticated user.
  - `role == DISTRIBUTOR`.
  - Active user.
  - Active and approved distributor party after Stage 3.
  - Matching `company`.

## 2.6 Query scoping

Provide reusable query and service helpers:

~~~python
QuerySet.for_company(company)
QuerySet.for_user(user)
~~~

Rules:

- Owner queries are limited to the Owner's company.
- Distributor queries are limited to the user's company and own party.
- Missing scope must return no records rather than all records.
- IDs supplied by a Distributor must never be trusted without queryset scoping.

## 2.7 Fiscal-period requirements

- A new company starts with the current period open.
- Only one open fiscal period may contain a transaction date.
- Posting into `CLOSED` or `LOCKED` is blocked.
- Reopening a closed period is prohibited in version one.
- Corrections after close are current-period reversal/adjustment entries linked to the original.
- `LOCKED` is stronger than `CLOSED` and reserved for finalized periods.

## 2.8 UI requirements

Owner pages:

- Company profile.
- Owner user management.
- Distributor invitation placeholder.
- Fiscal-period list.
- Document-sequence list.
- Audit-event list.

Distributor pages:

- Login.
- Password reset.
- Account-pending-approval screen.
- Profile placeholder.

## 2.9 APIs

| Method | Endpoint |
|---|---|
| POST | `/api/v1/auth/login/` |
| POST | `/api/v1/auth/logout/` |
| POST | `/api/v1/auth/password/reset/` |
| GET | `/api/v1/auth/me/` |
| GET/PATCH | `/api/v1/company/` |
| GET | `/api/v1/fiscal-periods/` |
| POST | `/api/v1/fiscal-periods/{id}/close/` |
| GET | `/api/v1/audit-events/` |

Distributor must not access company configuration, fiscal-period mutation or general audit APIs.

## 2.10 Tests

- Owner and Distributor login tests.
- Inactive account tests.
- Cross-company access denial.
- Distributor access denial for Owner endpoints.
- Fiscal-period posting guard.
- Sequence concurrency test.
- Audit actor test.
- Password reset and session invalidation tests.

## 2.11 Definition of Done

- Custom user model is in place before later business migrations.
- Only Owner and Distributor roles exist.
- Company scoping is enforced in base querysets and API mixins.
- Fiscal periods and document sequences work under concurrent requests.
- Permission tests prove a Distributor cannot access Owner-only resources.

---

# Stage 3 — Parties, Product Catalog, Pricing and Locations

## 3.1 Objective

Create the complete master-data foundation required by inventory, procurement and sales.

## 3.2 Django applications

- `parties`
- `catalog`
- `warehouse`
- `accounts`

## 3.3 Party models

### `Party`

- `company`
- `code`
- `name`
- `legal_name`
- `ntn`
- `strn`
- `territory`
- `email`
- `phone`
- `website`
- `billing_address`
- `shipping_address`
- `credit_notes`
- `active`

Unique on company and code.

### `PartyRole`

- `party`
- `role`: `MANUFACTURER`, `SUPPLIER`, `DISTRIBUTOR`, `RETAILER`, `COURIER`, `CUSTOMER`
- `active_from`
- `active_to`

Unique on party and role for overlapping active periods.

### `DistributorProfile`

- `party`
- `user`
- `approval_status`: `PENDING`, `APPROVED`, `SUSPENDED`, `REJECTED`
- `approved_by`
- `approved_at`
- `default_delivery_address`
- `territory`
- `notes`

Rules:

- User role must be `DISTRIBUTOR`.
- User and Party must belong to the same company.
- Party must have the `DISTRIBUTOR` role.
- One active Distributor user maps to one Distributor Party in version one.

### `PartyContact`

- `party`
- `name`
- `job_title`
- `phone`
- `email`
- `is_primary`

### `PartyAddress`

- `party`
- `address_type`
- `line_1`
- `line_2`
- `city`
- `province`
- `postal_code`
- `country`
- `is_default`

## 3.4 Catalog models

### `ProductCategory`

- `company`
- `name`
- `parent`
- `sort_order`
- `active`

### `Product`

- `company`
- `category`
- `sku`
- `barcode`
- `name`
- `generic_name`
- `brand_name`
- `indications`
- `enlistment_number`
- `form`
- `pack_size`
- `unit_of_measure`
- `units_per_case`
- `serving_size`
- `strength_basis`: `PER_CAPSULE`, `PER_SERVING`, `PER_TABLET`, `PER_ML`, `OTHER`
- `base_retail_price`
- `currency`
- `shelf_life_months`
- `hs_code`
- `default_reorder_point`
- `default_reorder_quantity`
- `allow_fractional_quantity`
- `active`

Unique on company and SKU. Barcode, when present, must be unique within the company.

### `Ingredient`

- `company`
- `name`
- `botanical_name`
- `part_used`
- `default_unit`
- `active`

### `ProductIngredient`

- `product`
- `ingredient`
- `strength`
- `unit`
- `is_medicinal`
- `extract_ratio`
- `equivalent_to`
- `sort_order`

## 3.5 Pricing models

### `ChannelPrice`

- `company`
- `product`
- `channel`: `SHOPIFY`, `DIRECT`, `DISTRIBUTOR`
- `price`
- `currency`
- `effective_from`
- `effective_to`
- `active`

No overlapping active price periods for the same company, product and channel.

Distributor-specific pricing is defined later through `DistributorAgreement` and `AgreementLine`. Price resolution order will be:

~~~text
AgreementLine special price
→ AgreementLine discount on distributor channel price
→ Agreement default discount on distributor channel price
→ Distributor channel price
→ Product base retail price
~~~

Every order and invoice line snapshots the resolved values.

## 3.6 Warehouse and location models

### `Location`

- `company`
- `code`
- `name`
- `location_type`
- `party` where required
- `on_book`
- `is_physical`
- `is_sellable`
- `address`
- `area_square_feet`
- `capacity_units`
- `active`

Location types:

| Type | On book | Physical | Sellable |
|---|---:|---:|---:|
| `OWN` | Yes | Yes | Yes |
| `IN_TRANSIT` | Yes | Logical | No |
| `CONSIGNMENT` | Yes | Yes | Conditionally |
| `QUARANTINE` | Yes | Yes | No |
| `DAMAGED_HOLD` | Yes | Yes | No |
| `EXPIRED_HOLD` | Yes | Yes | No |
| `DISPOSAL` | No | No | No |
| `SUPPLIER` | No | No | No |
| `CUSTOMER` | No | No | No |

Business rules:

- Supplier pseudo-locations require a supplier/manufacturer Party.
- Consignment locations require a Distributor Party.
- Customer locations may be generic or party-specific.
- Capacity is advisory and creates warnings; it does not block receipts.
- A location type cannot be changed after stock movements exist.
- `SHOPIFY_POOL` is not a stock location in the corrected design.

### `ChannelAllocation`

- `company`
- `product`
- `warehouse_location`
- `channel`
- `allocation_limit`
- `active`
- `updated_by`
- `updated_at`

### `ChannelAllocationEntry`

Immutable audit ledger:

- `channel_allocation`
- `previous_limit`
- `new_limit`
- `reason`
- `created_by`
- `created_at`

This records online allocation decisions without pretending physical stock moved.

## 3.7 Master-data workflows

### Distributor onboarding

~~~text
Owner creates Party
→ adds DISTRIBUTOR PartyRole
→ creates DistributorProfile
→ sends account invitation
→ Distributor sets password
→ Owner approves profile
→ Distributor can access portal
~~~

### Product setup

~~~text
Owner creates category
→ creates product and SKU
→ enters ingredients
→ adds channel prices
→ configures reorder defaults
→ activates product
~~~

### Location setup

~~~text
Owner creates physical OWN location
→ creates supplier pseudo-locations
→ creates generic customer/disposal locations
→ creates distributor consignment locations when required
~~~

## 3.8 UI requirements

Owner:

- Party list, form and detail.
- Distributor approval screen.
- Product/category list and form.
- Ingredient entry nested within product.
- Channel-price history.
- Location list, utilization warning and detail.
- Channel-allocation form and change history.

Distributor:

- Own profile.
- Own contacts and permitted addresses.
- Read-only eligible product catalog.
- No cost, manufacturer or company-wide location fields.

## 3.9 APIs

| Method | Endpoint |
|---|---|
| GET/POST | `/api/v1/parties/` |
| GET/PATCH | `/api/v1/parties/{id}/` |
| POST | `/api/v1/distributors/{id}/approve/` |
| POST | `/api/v1/distributors/{id}/suspend/` |
| GET/POST | `/api/v1/products/` |
| GET/PATCH | `/api/v1/products/{id}/` |
| GET/POST | `/api/v1/channel-prices/` |
| GET/POST | `/api/v1/locations/` |
| GET/POST | `/api/v1/channel-allocations/` |
| GET | `/api/v1/distributor/catalog/` |
| GET/PATCH | `/api/v1/distributor/profile/` |

## 3.10 Validations

- Distributor user cannot exist without a matching Distributor Party after approval.
- Manufacturer and supplier records do not receive logins.
- Product SKU cannot be changed after posted movements unless an audited migration procedure is used.
- Product prices cannot be negative.
- Effective price dates cannot overlap.
- Allocation limit cannot be negative.
- Allocation does not guarantee availability; it is capped by current sellable stock.
- Location party role must match the location type.

## 3.11 Tests

- Multi-role Party tests.
- Distributor profile mapping and approval tests.
- Cross-distributor data isolation tests.
- SKU/barcode uniqueness tests.
- Price-effective-date and resolution tests.
- Location truth-table tests.
- Channel-allocation audit-entry tests.
- Serializer tests proving cost/manufacturer data is absent from distributor responses.

## 3.12 Definition of Done

- All parties, products, prices and locations can be configured.
- Only approved Distributors can access the product catalog.
- Distributor catalog responses contain no sensitive fields.
- Pricing resolution is deterministic and tested.
- Shopify allocation is represented separately from physical stock.
- Master-data audit history is available to the Owner.

---

# Stage 4 — Inventory Ledger, Batches, Reservations and Stock Control

## 4.1 Objective

Build the authoritative inventory engine. No later stage may change stock except through the services completed here.

## 4.2 Django applications

- `inventory`
- `warehouse`
- `catalog`
- `notifications`
- `audit`

## 4.3 Models

### `Batch`

- `company`
- `product`
- `batch_code`
- `manufacturer`
- `manufacture_date`
- `expiry_date`
- `source_document_type`
- `source_document_id`
- `base_unit_cost`
- `landed_unit_cost`
- `currency`
- `status`: `QUARANTINE`, `RELEASED`, `HOLD`, `EXPIRED`, `RECALLED`
- `coa_file`
- `release_notes`
- `released_by`
- `released_at`
- `created_at`

Unique on company, product and batch code.

### `StockMovement`

Append-only:

- `company`
- `movement_number`
- `movement_type`
- `product`
- `batch`
- `from_location`
- `to_location`
- `quantity`
- `occurred_at`
- `posted_at`
- `fiscal_period`
- `channel`
- `source_document_type`
- `source_document_id`
- `idempotency_key`
- `unit_cost`
- `currency`
- `created_by`
- `reason`
- `reversal_of`

Movement types:

- `OPENING`
- `RECEIPT`
- `RELEASE`
- `TRANSFER`
- `DISPATCH`
- `SALES_RETURN`
- `PURCHASE_RETURN`
- `CONSIGNMENT_OUT`
- `SELL_THROUGH`
- `DAMAGE_HOLD`
- `EXPIRY_HOLD`
- `DISPOSAL`
- `COUNT_CORRECTION`
- `ADJUSTMENT`
- `REVERSAL`

Database protections must reject `UPDATE` and `DELETE` on posted movements.

### `StockBalance`

Derived cache:

- `company`
- `product`
- `batch`
- `location`
- `quantity_on_hand`
- `last_movement`
- `updated_at`

Unique on company, product, batch and location.

### `Reservation`

- `company`
- `product`
- `location`
- `channel`
- `quantity`
- `source_document_type`
- `source_document_id`
- `source_line_id`
- `status`: `ACTIVE`, `RELEASED`, `CONSUMED`, `EXPIRED`
- `expires_at`
- `created_by`
- `created_at`
- `released_at`

Reservations are product/location-level, not batch-level. Batch selection happens when picking or dispatching.

### `StockTransfer`

- `company`
- `transfer_number`
- `source_location`
- `in_transit_location`
- `destination_location`
- `status`: `DRAFT`, `APPROVED`, `DISPATCHED`, `PARTIALLY_RECEIVED`, `RECEIVED`, `CANCELLED`
- `requested_at`
- `approved_by`
- `dispatched_at`
- `received_at`
- `reason`

### `StockTransferLine`

- `transfer`
- `product`
- `batch`
- `requested_quantity`
- `dispatched_quantity`
- `received_quantity`
- `damaged_quantity`

### `StockCount`

- `company`
- `count_number`
- `location`
- `status`: `DRAFT`, `IN_PROGRESS`, `SUBMITTED`, `POSTED`, `CANCELLED`
- `count_date`
- `freeze_reference_time`
- `submitted_by`
- `posted_by`
- `posted_at`
- `notes`

### `StockCountLine`

- `stock_count`
- `product`
- `batch`
- `system_quantity`
- `counted_quantity`
- `variance`
- `reason`

### `ReorderRule`

- `company`
- `product`
- `location`
- `minimum_quantity`
- `target_quantity`
- `safety_stock`
- `supplier_lead_days`
- `active`

## 4.4 Required inventory service

Implement one authoritative service:

~~~python
post_stock_movement(
    *,
    company,
    movement_type,
    product,
    batch,
    from_location,
    to_location,
    quantity,
    occurred_at,
    source_document,
    user,
    reason=None,
    idempotency_key=None,
)
~~~

The service must:

1. Open `transaction.atomic()`.
2. Validate company consistency.
3. Validate the fiscal period is open.
4. Validate product, batch and locations are active.
5. Validate the batch belongs to the product.
6. Validate source and destination differ.
7. Validate quantity is positive.
8. Lock affected `StockBalance` rows with `select_for_update()`.
9. Verify sufficient source stock for on-book source locations.
10. Create one immutable `StockMovement`.
11. Decrease the source balance.
12. Increase the destination balance.
13. Store snapshotted unit cost.
14. Trigger low-stock and expiry evaluation after commit.
15. Return the existing movement when the idempotency key has already been processed.

No view, serializer, admin action, command or task may modify `StockBalance` directly.

## 4.5 Balance formulas

~~~text
on_hand(product, location)
    = sum of batch StockBalance quantities

active_reserved(product, location)
    = sum of ACTIVE Reservation quantities

available(product, location)
    = on_hand − active_reserved

shopify_publishable(product, location)
    = max(
        0,
        min(
            channel_allocation_limit − active_shopify_reservations,
            on_hand − all_active_reservations
        )
      )
~~~

## 4.6 Batch selection

- Default pick suggestion is FEFO: earliest valid expiry first.
- Expired, recalled, hold and quarantine batches cannot be selected for sale.
- Stock expiring within the configured warning window displays a warning.
- Owner may override FEFO with a mandatory reason.
- The chosen batch is snapshotted on dispatch/transfer lines.

## 4.7 Location workflows

### Owned transfer

~~~text
Owner creates transfer
→ approves transfer
→ source stock moves OWN → IN_TRANSIT
→ destination receives
→ stock moves IN_TRANSIT → destination OWN
~~~

Short receipt, damage or loss must be documented. Remaining in-transit quantity cannot disappear.

### Damage workflow

~~~text
OWN → DAMAGED_HOLD
→ Owner inspects
→ either DAMAGED_HOLD → OWN
→ or DAMAGED_HOLD → DISPOSAL
~~~

### Expiry workflow

~~~text
Daily job flags batch EXPIRED
→ Owner confirms physical isolation
→ OWN → EXPIRED_HOLD
→ approved write-off
→ EXPIRED_HOLD → DISPOSAL
~~~

### Stock count workflow

~~~text
Count starts with system snapshot
→ Owner enters physical counts
→ variance is calculated
→ Owner submits with reasons
→ posting creates COUNT_CORRECTION movements
→ balances are never directly overwritten
~~~

## 4.8 Opening inventory

Opening inventory import must require:

- Product.
- Batch code.
- Manufacture and expiry dates.
- Location.
- Quantity.
- Unit cost.
- Effective opening date.
- Source spreadsheet reference.

Opening stock posts from an `OPENING_EQUITY` or approved external pseudo-location into the destination. Import must be idempotent and produce a reconciliation report.

## 4.9 Alerts

Create alerts for:

- Available quantity at or below reorder point.
- Zero stock.
- Negative balance attempt.
- Batch expiry at 90, 60 and 30 days.
- Expired batch still in a sellable location.
- Stock transfer overdue in transit.
- Capacity utilization above configured warning percentage.
- Stock-balance rebuild mismatch.

## 4.10 UI requirements

Owner:

- Inventory dashboard by product and location.
- Product/batch stock detail.
- Drill-down from balance to movements.
- Transfer create, dispatch and receive screens.
- Stock-count screen optimized for phone and barcode input.
- Batch release/hold/recall screens.
- Damage and expiry isolation screens.
- Adjustment/reversal screen with mandatory reason.
- Reorder and expiry alert screens.

Every displayed quantity must show an as-of timestamp.

Distributor:

- No general inventory ledger.
- Only allowed availability indicator and own consignment balance.
- No batch cost or total company quantity.

## 4.11 APIs

| Method | Endpoint |
|---|---|
| GET | `/api/v1/inventory/balances/` |
| GET | `/api/v1/inventory/movements/` |
| POST | `/api/v1/inventory/adjustments/` |
| POST | `/api/v1/inventory/movements/{id}/reverse/` |
| GET/POST | `/api/v1/inventory/transfers/` |
| POST | `/api/v1/inventory/transfers/{id}/approve/` |
| POST | `/api/v1/inventory/transfers/{id}/dispatch/` |
| POST | `/api/v1/inventory/transfers/{id}/receive/` |
| GET/POST | `/api/v1/inventory/counts/` |
| POST | `/api/v1/inventory/counts/{id}/post/` |
| GET/POST | `/api/v1/inventory/reorder-rules/` |

All mutation endpoints are Owner-only.

## 4.12 Management commands

- `rebuild_stock_balances`
- `reconcile_stock_balances`
- `import_opening_stock`
- `check_expiry`
- `check_reorder_points`

Rebuild must calculate balances entirely from movements and report zero difference against the cache.

## 4.13 Tests

- Movement double-entry test.
- Direct balance-write prevention test.
- Movement update/delete database protection test.
- Concurrent dispatch test.
- Idempotent posting test.
- Reservation availability test.
- Transfer in-transit test.
- Partial receipt test.
- FEFO suggestion and override tests.
- Expired/held batch hard-block tests.
- Stock-count variance posting test.
- Damage and disposal accounting-location tests.
- Closed-period posting test.
- Balance rebuild reconciliation test.
- Distributor inventory isolation tests.

## 4.14 Definition of Done

- Every stock change is represented by an immutable movement.
- Balance cache rebuilds exactly from the ledger.
- Concurrent requests cannot oversell.
- Transfers preserve in-transit stock.
- Batches and expiry restrictions are enforced.
- Owner can explain every quantity through drill-down.
- No later stage is permitted to bypass this inventory service.

---

# Stage 5 — Procurement, Goods Receiving and Landed Cost

## 5.1 Objective

Manage purchasing of finished goods from contract manufacturers or suppliers, including approval, receiving, batch creation, landed cost and supplier financial documents.

## 5.2 Django applications

- `procurement`
- `inventory`
- `parties`
- `finance`
- `audit`

## 5.3 Models

### `PurchaseOrder`

- `company`
- `purchase_order_number`
- `supplier`
- `manufacturer`
- `order_date`
- `expected_date`
- `currency`
- `exchange_rate`
- `status`: `DRAFT`, `SUBMITTED`, `APPROVED`, `PARTIALLY_RECEIVED`, `RECEIVED`, `CLOSED`, `CANCELLED`
- `subtotal`
- `discount`
- `tax`
- `total`
- `terms`
- `notes`
- `submitted_by`
- `approved_by`
- `approved_at`

### `PurchaseOrderLine`

- `purchase_order`
- `product`
- `ordered_quantity`
- `received_quantity`
- `unit_price`
- `discount`
- `tax`
- `line_total`

### `GoodsReceipt`

- `company`
- `goods_receipt_number`
- `purchase_order`
- `supplier`
- `receiving_location`
- `receipt_date`
- `status`: `DRAFT`, `POSTED`, `REVERSED`
- `supplier_delivery_reference`
- `received_by`
- `posted_by`
- `posted_at`
- `notes`

### `GoodsReceiptLine`

- `goods_receipt`
- `purchase_order_line`
- `product`
- `batch_code`
- `manufacture_date`
- `expiry_date`
- `received_quantity`
- `accepted_quantity`
- `rejected_quantity`
- `base_unit_cost`
- `allocated_landed_cost`
- `final_landed_unit_cost`
- `coa_file`

### `LandedCostAllocation`

- `company`
- `goods_receipt`
- `cost_type`: `FREIGHT`, `LABELLING`, `TESTING`, `DUTY`, `CLEARING`, `INSURANCE`, `OTHER`
- `amount`
- `currency`
- `exchange_rate`
- `allocation_basis`: `VALUE`, `QUANTITY`, `WEIGHT`, `MANUAL`
- `expense_reference`
- `notes`

### `SupplierInvoice`

- `company`
- `supplier`
- `purchase_order`
- `goods_receipt`
- `supplier_invoice_number`
- `invoice_date`
- `due_date`
- `currency`
- `exchange_rate`
- `subtotal`
- `tax`
- `total`
- `status`
- `attachment`

### `SupplierPayment`

- `company`
- `supplier`
- `supplier_invoice`
- `payment_date`
- `amount`
- `currency`
- `payment_method`
- `reference`
- `attachment`
- `posted_by`

## 5.4 Purchase workflow

~~~text
Owner creates Purchase Order draft
→ submits
→ approves
→ supplier delivers
→ Owner records Goods Receipt
→ batches and landed costs are calculated
→ receipt is posted
→ stock moves SUPPLIER → QUARANTINE
→ Owner reviews documents/COA
→ batch released
→ stock moves QUARANTINE → OWN
→ supplier invoice and payment are recorded
~~~

The same Owner may submit and approve in version one, but both actions and timestamps must still be recorded.

## 5.5 Goods-receipt posting rules

- Purchase order must be approved.
- Supplier must match the purchase order.
- Receiving location must be `QUARANTINE` or approved `OWN`.
- Product must exist on the purchase-order line.
- Accepted plus rejected quantity must equal received quantity.
- Cumulative accepted quantity cannot exceed ordered quantity without an Owner override and reason.
- Batch code, manufacture date and expiry date are required.
- Expiry must be after manufacture date and receipt date.
- Duplicate batch handling must be explicit: add to matching valid batch or reject.
- Posting creates batches and `RECEIPT` movements.
- Posted receipt cannot be edited.
- Reversal must reverse all associated movements and financial effects.

## 5.6 Landed-cost calculation

Base receipt value:

~~~text
line_base_value = accepted_quantity × purchase_unit_price
~~~

Allocated cost:

~~~text
VALUE basis:
  line_share = line_base_value / total_base_value

QUANTITY basis:
  line_share = accepted_quantity / total_accepted_quantity

allocated_cost = landed_cost_amount × line_share

final_landed_unit_cost =
  (line_base_value + all allocated costs) / accepted_quantity
~~~

The final landed unit cost is snapshotted on the Batch and on receipt movements. Later changes require a controlled cost-adjustment workflow, not editing historical movements.

## 5.7 Purchase returns

~~~text
Owner creates Purchase Return
→ selects original receipt and batch
→ stock moves OWN/QUARANTINE → SUPPLIER
→ supplier debit/credit effect is recorded
~~~

Return quantity cannot exceed available quantity from the referenced batch.

## 5.8 UI requirements

- Purchase-order list, create and approval.
- Outstanding purchase-order report.
- Mobile-friendly goods-receipt form.
- Batch and COA upload.
- Landed-cost allocation preview.
- Posting confirmation showing quantities, destination, cost and resulting batch status.
- Supplier invoice and payment screens.
- Purchase-return screen.

## 5.9 APIs

| Method | Endpoint |
|---|---|
| GET/POST | `/api/v1/procurement/purchase-orders/` |
| POST | `/api/v1/procurement/purchase-orders/{id}/submit/` |
| POST | `/api/v1/procurement/purchase-orders/{id}/approve/` |
| GET/POST | `/api/v1/procurement/goods-receipts/` |
| POST | `/api/v1/procurement/goods-receipts/{id}/post/` |
| POST | `/api/v1/procurement/goods-receipts/{id}/reverse/` |
| GET/POST | `/api/v1/procurement/landed-costs/` |
| GET/POST | `/api/v1/procurement/supplier-invoices/` |
| GET/POST | `/api/v1/procurement/supplier-payments/` |
| GET/POST | `/api/v1/procurement/purchase-returns/` |

All endpoints are Owner-only.

## 5.10 Tests

- Purchase-order status transition tests.
- Over-receipt validation and override tests.
- Partial receipt tests.
- Duplicate receipt idempotency test.
- Batch creation test.
- Quarantine receipt and release test.
- Landed-cost allocation tests for every basis.
- Currency conversion precision test.
- Receipt reversal test.
- Purchase-return stock and supplier-effect test.
- Closed-period posting test.

## 5.11 Definition of Done

- Owner can purchase and receive products by batch.
- Every accepted unit enters through an inventory movement.
- Landed unit cost is reproducible from its source costs.
- Partial receipts and purchase returns reconcile.
- Posted purchase documents are immutable and reversible.
- Supplier balances can be derived from invoices and payments.

---

# Stage 6 — Distributor Agreements, Sales, Consignment and Portal

## 6.1 Objective

Implement the complete distributor lifecycle: agreements, price resolution, stock requests, approval, reservation, dispatch, invoicing, payments, outstanding balances, consignment sell-through and returns.

## 6.2 Django applications

- `sales`
- `inventory`
- `parties`
- `finance`
- `notifications`
- `audit`

## 6.3 Models

### `DistributorAgreement`

- `company`
- `distributor`
- `agreement_number`
- `default_terms`: `SELL_IN` or `CONSIGNMENT`
- `trade_discount_percentage`
- `commission_percentage`
- `credit_limit`
- `credit_days`
- `territory`
- `effective_from`
- `effective_to`
- `status`: `DRAFT`, `ACTIVE`, `SUSPENDED`, `EXPIRED`
- `approved_by`
- `approved_at`

### `AgreementLine`

- `agreement`
- `product`
- `terms_override`
- `special_unit_price`
- `discount_percentage_override`
- `commission_percentage_override`
- `minimum_order_quantity`
- `maximum_order_quantity`
- `active`

### `StockRequest`

- `company`
- `request_number`
- `distributor`
- `requested_delivery_date`
- `delivery_address`
- `status`: `DRAFT`, `SUBMITTED`, `UNDER_REVIEW`, `APPROVED`, `PARTIALLY_APPROVED`, `REJECTED`, `CONVERTED`, `CANCELLED`
- `submitted_at`
- `reviewed_by`
- `review_notes`

### `StockRequestLine`

- `stock_request`
- `product`
- `requested_quantity`
- `approved_quantity`
- `rejection_reason`

### `SalesOrder`

- `company`
- `order_number`
- `channel`: `DISTRIBUTOR`, `DIRECT`, `SHOPIFY`
- `distributor`
- `customer`
- `source_request`
- `agreement`
- `terms`
- `order_date`
- `delivery_date`
- `fulfilment_location`
- `currency`
- `status`: `DRAFT`, `PENDING_APPROVAL`, `APPROVED`, `PROCESSING`, `PARTIALLY_DISPATCHED`, `DISPATCHED`, `DELIVERED`, `CANCELLED`, `CLOSED`
- `payment_status`
- `subtotal`
- `discount`
- `tax`
- `shipping`
- `total`
- `external_order_id`
- `approved_by`
- `approved_at`

### `SalesOrderLine`

- `sales_order`
- `product`
- `quantity`
- `fulfilled_quantity`
- `terms`
- `list_unit_price`
- `resolved_unit_price`
- `discount_percentage`
- `discount_amount`
- `commission_percentage`
- `tax_amount`
- `line_total`
- `pricing_source`

### `Dispatch`

- `company`
- `dispatch_number`
- `sales_order`
- `source_location`
- `destination_location`
- `dispatch_date`
- `status`: `DRAFT`, `POSTED`, `DELIVERED`, `RETURNED`, `REVERSED`
- `posted_by`
- `posted_at`
- `delivery_reference`

### `DispatchLine`

- `dispatch`
- `sales_order_line`
- `product`
- `batch`
- `quantity`
- `unit_cost`
- `expiry_date`

### `SalesInvoice`

- `company`
- `invoice_number`
- `sales_order`
- `dispatch`
- `party`
- `invoice_date`
- `due_date`
- `currency`
- `status`: `DRAFT`, `POSTED`, `PARTIALLY_PAID`, `PAID`, `CREDITED`, `VOID`
- `subtotal`
- `discount`
- `tax`
- `commission`
- `total`
- `posted_by`

`outstanding` is exposed as a computed property or queryset annotation. It is not an editable database field.

### `SalesInvoiceLine`

- `invoice`
- `product`
- `batch`
- `quantity`
- `unit_price`
- `discount`
- `tax`
- `commission`
- `unit_cost`
- `line_total`

### `Receipt`

- `company`
- `receipt_number`
- `party`
- `receipt_date`
- `amount`
- `currency`
- `payment_method`
- `reference`
- `status`: `DRAFT`, `POSTED`, `REVERSED`
- `attachment`
- `posted_by`

### `ReceiptAllocation`

- `receipt`
- `invoice`
- `amount`

### `PartyLedgerEntry`

Immutable:

- `company`
- `party`
- `entry_number`
- `entry_type`: `OPENING_BALANCE`, `INVOICE`, `RECEIPT`, `CREDIT_NOTE`, `DEBIT_NOTE`, `ADJUSTMENT`, `REVERSAL`
- `transaction_date`
- `fiscal_period`
- `debit`
- `credit`
- `currency`
- `source_document_type`
- `source_document_id`
- `reversal_of`
- `created_by`
- `created_at`

### `SellThroughReport`

- `company`
- `report_number`
- `distributor`
- `consignment_location`
- `period_start`
- `period_end`
- `status`: `DRAFT`, `SUBMITTED`, `APPROVED`, `POSTED`, `REJECTED`
- `submitted_by`
- `approved_by`
- `posted_at`

### `SellThroughLine`

- `sell_through_report`
- `product`
- `batch`
- `opening_quantity`
- `received_quantity`
- `sold_quantity`
- `returned_quantity`
- `closing_quantity`
- `retail_unit_price`
- `commission_percentage`

### `SalesReturn`

- `company`
- `return_number`
- `party`
- `original_invoice`
- `original_dispatch`
- `return_date`
- `status`: `REQUESTED`, `APPROVED`, `RECEIVED`, `INSPECTED`, `POSTED`, `REJECTED`
- `reason`
- `requested_by`
- `approved_by`

### `SalesReturnLine`

- `sales_return`
- `invoice_line`
- `product`
- `batch`
- `quantity`
- `condition`: `SELLABLE`, `DAMAGED`, `EXPIRED`, `OTHER`
- `destination_location`
- `credit_amount`

### `CreditNote`

- `company`
- `credit_note_number`
- `party`
- `invoice`
- `sales_return`
- `date`
- `amount`
- `reason`
- `status`
- `posted_by`

## 6.4 Price and term resolution

Resolve per order line:

~~~text
1. Active AgreementLine for distributor + product
2. Active DistributorAgreement default
3. Active DISTRIBUTOR ChannelPrice
4. Product base retail price
~~~

Rules:

- `special_unit_price` takes priority over percentage discount.
- Sell-in distributor margin is a discount; it is not also recorded as commission expense.
- Consignment uses retail price and commission.
- Final price, discount, terms and commission are snapshotted on the order line.
- Agreement changes never rewrite historical documents.

## 6.5 Credit validation

~~~text
current_outstanding
    = sum PartyLedgerEntry.debit − sum PartyLedgerEntry.credit

available_credit
    = credit_limit − current_outstanding

projected_outstanding
    = current_outstanding + proposed_invoice_total
~~~

Hard-block approval or dispatch when:

- Agreement is inactive/suspended.
- Distributor is suspended.
- Existing invoices are overdue beyond policy.
- Projected outstanding exceeds credit limit.

Owner override may be added only if the final approved business policy allows it. If enabled, require reason, timestamp and audit event.

## 6.6 Sell-in workflow

~~~text
Distributor creates Stock Request
→ submits
→ Owner reviews requested vs approved quantity
→ request converts to Sales Order
→ Owner approves
→ inventory Reservation created
→ Owner selects FEFO batches
→ Dispatch posts OWN → CUSTOMER
→ reservation consumed
→ invoice and PartyLedger debit posted
→ Distributor records payment
→ Owner posts Receipt and PartyLedger credit
~~~

Revenue-recognition timing must be configurable based on approved accounting policy; dispatch and delivery timestamps must both be retained.

## 6.7 Consignment workflow

~~~text
Distributor Stock Request
→ Owner approval and Reservation
→ Dispatch OWN → distributor CONSIGNMENT location
→ no sell-in invoice
→ Distributor submits Sell-Through Report
→ Owner approves
→ stock moves CONSIGNMENT → CUSTOMER
→ invoice generated for sold quantity
→ commission calculated
→ PartyLedger debit posted
~~~

Unsold consignment stock remains an on-book company asset.

## 6.8 Cancellation

- Draft or submitted request may be cancelled without ledger movement.
- Approved order cancellation releases active reservations.
- Posted dispatch cannot be cancelled; it must be reversed or returned.
- Released reservations do not create stock movements.

## 6.9 Returns

~~~text
Distributor requests return against invoice
→ Owner approves
→ goods physically received
→ Owner inspects condition
→ stock moves CUSTOMER/CONSIGNMENT → selected hold or OWN location
→ Credit Note posts PartyLedger credit
~~~

Return quantity cannot exceed invoiced quantity minus previously returned quantity.

## 6.10 Distributor portal

Required pages:

- Dashboard showing own outstanding, available credit, overdue amount and recent order status.
- Eligible product catalog with distributor price.
- Create/edit/submit stock request.
- Own requests and orders.
- Own dispatches with batch/expiry where permitted.
- Own invoices and credit notes.
- Own receipts and allocations.
- Own ledger statement.
- Own consignment inventory.
- Sell-through report entry.
- Return-request form.
- Downloadable PDFs/CSVs for own records.

No portal response may contain manufacturer cost, landed cost, company margin, another Party ID or company-wide stock.

## 6.11 Owner UI

- Distributor agreement and product overrides.
- Credit and overdue dashboard.
- Stock-request approval with requested and approved quantities.
- Order reservation and availability preview.
- Batch-picking and dispatch confirmation.
- Invoice posting.
- Receipt allocation.
- Consignment reconciliation.
- Return inspection and credit-note posting.
- Distributor ledger drill-down.

## 6.12 APIs

Owner APIs:

| Method | Endpoint |
|---|---|
| GET/POST | `/api/v1/sales/agreements/` |
| GET/POST | `/api/v1/sales/stock-requests/` |
| POST | `/api/v1/sales/stock-requests/{id}/approve/` |
| GET/POST | `/api/v1/sales/orders/` |
| POST | `/api/v1/sales/orders/{id}/approve/` |
| GET/POST | `/api/v1/sales/dispatches/` |
| POST | `/api/v1/sales/dispatches/{id}/post/` |
| GET/POST | `/api/v1/sales/invoices/` |
| POST | `/api/v1/sales/invoices/{id}/post/` |
| GET/POST | `/api/v1/sales/receipts/` |
| GET/POST | `/api/v1/sales/returns/` |
| GET/POST | `/api/v1/sales/credit-notes/` |

Distributor APIs:

| Method | Endpoint |
|---|---|
| GET/POST | `/api/v1/distributor/stock-requests/` |
| POST | `/api/v1/distributor/stock-requests/{id}/submit/` |
| GET | `/api/v1/distributor/orders/` |
| GET | `/api/v1/distributor/dispatches/` |
| GET | `/api/v1/distributor/invoices/` |
| GET | `/api/v1/distributor/receipts/` |
| GET | `/api/v1/distributor/ledger/` |
| GET | `/api/v1/distributor/consignment-stock/` |
| GET/POST | `/api/v1/distributor/sell-through/` |
| GET/POST | `/api/v1/distributor/returns/` |

## 6.13 Notifications

- Stock request submitted.
- Request approved, partially approved or rejected.
- Order dispatched.
- Invoice posted.
- Payment recorded.
- Distributor near credit limit.
- Distributor over credit limit.
- Invoice approaching due date.
- Invoice overdue.
- Sell-through report due.
- Return approved or rejected.

## 6.14 Tests

- Agreement effective-date tests.
- Price-resolution hierarchy tests.
- Sell-in discount versus consignment commission tests.
- Distributor cross-row access attacks.
- Credit-limit and overdue hard-block tests.
- Request approval and conversion tests.
- Reservation creation/release/consumption tests.
- Partial dispatch tests.
- Sell-in dispatch movement test.
- Consignment movement and sell-through tests.
- Invoice and PartyLedger posting tests.
- Receipt allocation and outstanding-balance tests.
- Return quantity and condition-routing tests.
- Credit-note ledger test.
- Historical pricing immutability test.

## 6.15 Definition of Done

- An approved Distributor can complete their entire permitted workflow.
- A Distributor can never see another distributor's records.
- Sell-in and consignment are financially and operationally distinct.
- Every approved order reserves stock before dispatch.
- Dispatch consumes reservations and posts correct batch movements.
- Outstanding balance is reproducible from immutable ledger entries.
- Returns restore or isolate stock and create correct credits.

---

# Stage 7 — Shopify, Direct Sales, Logistics and COD

## 7.1 Objective

Integrate Shopify through the GraphQL Admin API, support direct/physical sales, manage fulfillment and courier shipments, and reconcile COD remittances without bypassing the inventory or financial services.

## 7.2 Django applications

- `channels_shopify`
- `sales`
- `logistics`
- `inventory`
- `finance`
- `notifications`
- `audit`

## 7.3 Shopify models

### `ShopifyStore`

- `company`
- `name`
- `shop_domain`
- `encrypted_access_token`
- `api_version`
- `default_fulfilment_location`
- `shopify_location_gid`
- `active`
- `last_successful_pull_at`
- `last_successful_reconciliation_at`
- `sync_status`
- `sync_error`

The encryption key must be stored outside the database.

### `ShopifyVariantMap`

- `company`
- `product`
- `shopify_product_gid`
- `shopify_variant_gid`
- `shopify_inventory_item_gid`
- `shopify_location_gid`
- `active`
- `last_verified_at`

Unique on store and Shopify variant ID. One active Shopify variant maps to one internal product.

### `ShopifyWebhookEvent`

Append-only:

- `store`
- `topic`
- `shopify_event_id`
- `api_version`
- `raw_payload`
- `hmac_verified`
- `received_at`
- `processed_at`
- `status`: `RECEIVED`, `PROCESSING`, `PROCESSED`, `FAILED`, `IGNORED`
- `attempts`
- `last_error`

Unique on store, topic and Shopify event ID.

### `ShopifyOrderMirror`

- `company`
- `store`
- `shopify_order_gid`
- `order_number`
- `updated_at_shopify`
- `financial_status`
- `fulfilment_status`
- `currency`
- `gross_amount`
- `discount_amount`
- `tax_amount`
- `shipping_amount`
- `refund_amount`
- `gateway_fees`
- `customer_snapshot`
- `shipping_address_snapshot`
- `raw_payload`
- `local_sales_order`
- `last_synced_at`

### `ShopifyInventoryPublishRun`

- `company`
- `store`
- `started_at`
- `completed_at`
- `triggered_by`
- `status`
- `pulled_orders_first`
- `summary`
- `error`

### `ShopifyInventoryPublishLine`

- `publish_run`
- `product`
- `shopify_inventory_item_gid`
- `shopify_location_gid`
- `shopify_quantity_before`
- `computed_quantity`
- `published_quantity`
- `result`
- `error`

## 7.4 Required webhook topics

At minimum:

- Order created.
- Order updated/cancelled.
- Fulfillment-related update where required.
- Refund created.
- Return events where supported and required.
- App uninstalled/token invalidated.

Webhook requirements:

1. Verify HMAC before processing.
2. Store raw event before business processing.
3. Respond quickly and process through Celery.
4. Deduplicate using Shopify event ID and topic.
5. Fetch authoritative GraphQL data when webhook payload is incomplete.
6. Retry transient failures with bounded exponential backoff.
7. Move repeatedly failing events to an Owner-visible failure queue.
8. Never log the access token.

## 7.5 Shopify order flow

~~~text
Webhook received
→ HMAC verified
→ event stored
→ Celery fetches current order through GraphQL
→ ShopifyOrderMirror upserted
→ variant IDs mapped to internal products
→ local SalesOrder created/updated idempotently
→ active Reservations created
→ Owner sees order in fulfillment queue
→ Owner selects valid batches
→ Dispatch posts OWN → CUSTOMER
→ reservations consumed
→ Shopify fulfillment updated
~~~

Rules:

- Import does not create a stock movement.
- Cancellation before dispatch releases reservations.
- Cancellation after dispatch requires return/reversal processing.
- Duplicate webhook or polling results must not duplicate orders or reservations.
- Unknown variant mapping places order in an exception queue and blocks fulfillment.
- Edited Shopify order quantities update reservations through a controlled difference.

## 7.6 Shopify inventory publication

Internal physical stock remains in `OWN`. Online allocation is controlled by `ChannelAllocation`.

~~~text
computed_publishable =
max(
  0,
  min(
    allocation_limit − active Shopify reservations,
    physical on-hand − all active reservations
  )
)
~~~

The publish action must:

1. Acquire a per-store advisory lock.
2. Pull or reconcile Shopify orders before calculation.
3. Recompute local publishable quantity.
4. Fetch current Shopify quantity.
5. Show or record the difference.
6. Publish with Shopify GraphQL `inventorySetQuantities`.
7. Use compare-and-set/concurrency protection where supported.
8. Save each result.
9. Advance sync timestamps only after success.

Version-one policy:

- Owner can perform manual reviewed publication.
- A nightly job detects drift.
- The system may automatically reduce an unsafe overpublished quantity if approved in configuration.
- Automatic increases are disabled by default and require Owner confirmation.
- A failed push must not change local inventory.

## 7.7 Polling fallback and reconciliation

Webhooks are primary. Polling is mandatory as a fallback:

- Pull orders every 30 minutes using a stored cursor/window.
- Use overlap time to protect against clock and delivery delay.
- Upsert by Shopify order ID.
- Advance cursor only after the full pull succeeds.
- Nightly compare Shopify published quantities with computed local quantities.
- Drift produces an alert and report; it must not silently change the stock ledger.

## 7.8 Direct and physical sales

Direct sales reuse `SalesOrder`, `Dispatch`, `SalesInvoice`, `Receipt` and inventory services with channel `DIRECT`.

Workflow:

~~~text
Owner selects customer or walk-in
→ selects products and quantities
→ system checks available stock
→ resolves DIRECT price
→ Owner selects payment method
→ order/dispatch/invoice/receipt posted
→ stock moves OWN → CUSTOMER
~~~

Requirements:

- Support named customer and generic walk-in customer.
- Support prepaid, cash, bank transfer and approved credit.
- Support receipt generation.
- Support tax lines even if tax is initially zero.
- Support free/bonus goods as zero-revenue lines that still consume stock and COGS.
- Free goods require a promotion/reason code.
- Direct return uses the same return inspection workflow as distributor returns.

## 7.9 Logistics models

### `Courier`

Uses a Party with `COURIER` role plus:

- service codes
- account reference
- contact settings
- active

### `Shipment`

- `company`
- `shipment_number`
- `dispatch`
- `courier`
- `tracking_number`
- `payment_mode`: `PREPAID`, `COD`
- `cod_amount`
- `shipping_charge`
- `status`: `DRAFT`, `BOOKED`, `IN_TRANSIT`, `DELIVERED`, `CASH_COLLECTED`, `REMITTED`, `RTO`, `CANCELLED`
- `booked_at`
- `delivered_at`
- `cash_collected_at`
- `remitted_at`
- `rto_at`
- `last_tracking_payload`

### `CODRemittance`

- `company`
- `courier`
- `remittance_number`
- `remittance_date`
- `gross_collected`
- `courier_charges`
- `tax_withheld`
- `other_deductions`
- `net_received`
- `bank_reference`
- `source_file`
- `status`: `DRAFT`, `MATCHED`, `PARTIALLY_MATCHED`, `POSTED`

### `CODRemittanceLine`

- `remittance`
- `shipment`
- `tracking_number`
- `cod_amount`
- `courier_charge`
- `tax_withheld`
- `other_deduction`
- `net_amount`
- `match_status`
- `difference`

## 7.10 RTO and refund workflows

### Return to origin

~~~text
Courier marks shipment RTO
→ Owner receives physical goods
→ condition inspected
→ stock moves CUSTOMER/in-transit source → OWN or hold location
→ revenue/receivable is reversed or credited
→ shipping/RTO charge recorded as expense
~~~

### Shopify refund

- A refund is not automatically a physical return.
- Refund creates or updates financial credit.
- Stock returns only after an approved physical return/restock decision.
- Partial refunds and partial returns must be supported independently.
- Restock location and batch must be explicit.

## 7.11 UI requirements

Owner:

- Shopify connection status and token-health view.
- Variant-mapping screen.
- Unmapped-order exception queue.
- Needs-fulfillment queue.
- Inventory publication difference preview.
- Sync-run and webhook failure logs.
- Direct sale/POS-style screen.
- Shipment booking and tracking view.
- COD remittance import and matching.
- RTO receiving and inspection.

Distributor:

- No Shopify screens.
- May see shipment/tracking attached to own dispatch.

## 7.12 APIs and integration services

| Method | Endpoint |
|---|---|
| POST | `/api/v1/shopify/webhooks/{topic}/` |
| GET | `/api/v1/shopify/stores/` |
| GET/POST | `/api/v1/shopify/variant-maps/` |
| POST | `/api/v1/shopify/pull-orders/` |
| POST | `/api/v1/shopify/inventory/preview/` |
| POST | `/api/v1/shopify/inventory/publish/` |
| GET | `/api/v1/shopify/sync-runs/` |
| GET/POST | `/api/v1/direct-sales/orders/` |
| POST | `/api/v1/direct-sales/orders/{id}/post/` |
| GET/POST | `/api/v1/logistics/shipments/` |
| GET/POST | `/api/v1/logistics/remittances/` |
| POST | `/api/v1/logistics/remittances/{id}/match/` |
| POST | `/api/v1/logistics/remittances/{id}/post/` |

## 7.13 Background jobs

| Job | Schedule |
|---|---|
| Process Shopify webhook | Event-driven |
| Pull Shopify orders | Every 30 minutes |
| Reconcile Shopify inventory | Nightly |
| Retry failed Shopify event | Bounded retry |
| Poll active courier shipments | Configurable |
| Check unmatched COD shipments | Daily |
| Check overdue remittance | Daily |

## 7.14 Tests

- Shopify HMAC verification.
- Duplicate webhook idempotency.
- Unknown variant exception handling.
- Order edit/cancel reservation tests.
- Concurrent store-sync advisory lock.
- Pull cursor success/failure tests.
- Inventory publication formula.
- Compare-and-set conflict handling.
- Drift reporting.
- Direct sale stock/finance integration.
- Free-goods COGS test.
- COD matching and difference test.
- RTO stock and finance reversal test.
- Refund without restock test.
- Distributor denial for all Shopify resources.

## 7.15 Definition of Done

- Shopify orders arrive automatically and idempotently.
- Polling recovers missed events.
- Shopify and local product mappings are explicit.
- Inventory publication cannot overwrite newer data blindly.
- Direct sales use the same inventory and finance truth.
- COD collections, charges and RTOs reconcile to shipments.
- Distributor users cannot trigger or inspect Shopify synchronization.

---

# Stage 8 — Management Finance, Profit, Capital and Period Close

## 8.1 Objective

Provide reliable management-finance reporting derived from posted operational data: revenue, COGS, variable costs, operating expenses, distributor/customer balances, supplier balances, profit declarations and equity-partner capital allocations.

## 8.2 Django applications

- `finance`
- `sales`
- `procurement`
- `inventory`
- `core`
- `audit`

## 8.3 Financial architecture requirement

The finance module must calculate traceable financial results from posted operational documents, maintain party and capital ledgers, support period closing, and make every reported value drillable to its source transactions.

## 8.4 Models

### `ExpenseCategory`

- `company`
- `name`
- `parent`
- `code`
- `default_cost_center`
- `active`

Seed categories:

- Marketing
- Salaries
- Rent
- Utilities
- Freight outbound
- Courier charges
- Regulatory and licence
- Packaging and labels
- Distributor commission
- Bank and gateway fees
- Professional fees
- Travel
- Depreciation
- Tax and withholding
- Other

### `Expense`

- `company`
- `expense_number`
- `category`
- `fiscal_period`
- `expense_date`
- `party`
- `description`
- `amount`
- `currency`
- `exchange_rate`
- `base_currency_amount`
- `cost_center`
- `allocation_basis`
- `status`: `DRAFT`, `POSTED`, `REVERSED`
- `attachment`
- `posted_by`
- `posted_at`
- `reversal_of`

### `CostAllocation`

- `expense`
- `product`
- `channel`
- `distributor`
- `allocated_amount`
- `allocation_percentage`
- `basis_note`

Allocated total cannot exceed expense base-currency amount.

### `TaxLine`

Reusable child of invoice/document:

- `company`
- `source_document_type`
- `source_document_id`
- `tax_type`
- `rate`
- `taxable_amount`
- `tax_amount`
- `withholding`

Tax calculations remain configurable and must be confirmed with the company's accountant before production.

### `PeriodSnapshot`

- `company`
- `fiscal_period`
- `product`
- `batch`
- `location`
- `closing_quantity`
- `closing_unit_cost`
- `closing_value`

### `PeriodChannelSummary`

- `company`
- `fiscal_period`
- `channel`
- `product`
- `quantity_sold`
- `gross_sales`
- `discounts`
- `returns`
- `net_revenue`
- `cogs`
- `commission`
- `gateway_fees`
- `freight_out`
- `contribution_margin`

### `ProfitDeclaration`

- `company`
- `fiscal_period`
- `net_revenue`
- `cogs`
- `gross_profit`
- `variable_selling_costs`
- `contribution_margin`
- `operating_expenses`
- `operating_profit`
- `tax_provision`
- `reserve_amount`
- `distributable_profit`
- `status`: `DRAFT`, `REVIEWED`, `APPROVED`
- `prepared_by`
- `approved_by`
- `approved_at`

### `PartnerAllocation`

- `profit_declaration`
- `equity_partner`
- `share_percentage`
- `allocated_amount`
- `paid_out`
- `to_reserve`
- `to_reinvestment`

The three destinations must sum to allocated amount.

### `CapitalEntry`

Immutable:

- `company`
- `equity_partner`
- `entry_type`: `OPENING`, `CONTRIBUTION`, `PROFIT_ALLOCATION`, `WITHDRAWAL`, `RESERVE_TRANSFER`, `REINVESTMENT`, `REVERSAL`
- `entry_date`
- `fiscal_period`
- `debit`
- `credit`
- `source_document_type`
- `source_document_id`
- `reversal_of`
- `created_by`

## 8.5 Authoritative calculations

### Revenue

~~~text
gross_sales
− sales discounts
− returns and credit notes
= net_revenue
~~~

### COGS

~~~text
cogs = sum(dispatched/sold quantity × snapshotted batch unit cost)
~~~

For consignment, COGS occurs on sell-through, not consignment dispatch.

### Profit levels

~~~text
gross_profit
    = net_revenue − cogs

variable_selling_costs
    = distributor commissions
    + Shopify/gateway fees
    + outbound freight
    + courier transaction costs

contribution_margin
    = gross_profit − variable_selling_costs

operating_profit
    = contribution_margin − unallocated operating expenses

distributable_profit
    = operating_profit − tax provision − reserve amount
~~~

Sell-in distributor discount reduces revenue. It must not also appear as a commission expense.

## 8.6 Revenue-recognition configuration

Store all operational timestamps and apply the accountant-approved policy.

Candidate recognition events:

| Channel | Configurable event |
|---|---|
| Distributor sell-in | Dispatch or confirmed delivery |
| Distributor consignment | Approved sell-through |
| Shopify prepaid | Fulfillment or delivery |
| Shopify COD | Delivery, cash collection or remittance based on approved policy |
| Direct sale | Posted invoice |

The final policy must be configured, documented and locked before production period close.

## 8.7 Party balances

`PartyLedgerEntry` is used for distributors, customers and suppliers.

The required sign convention is:

| Document | Ledger side |
|---|---|
| Sales invoice | Debit distributor/customer |
| Customer receipt | Credit distributor/customer |
| Sales credit note | Credit distributor/customer |
| Supplier invoice | Credit supplier |
| Supplier payment | Debit supplier |
| Supplier credit note | Debit supplier |

~~~text
customer_or_distributor_receivable
    = sum(debit) − sum(credit)

supplier_payable
    = sum(credit) − sum(debit)
~~~

Reports must not combine receivable and payable balances under one ambiguous unsigned value.

## 8.8 Multi-currency

- Store original currency amount.
- Store exchange rate used.
- Store base-currency amount.
- Exchange rate cannot change after posting.
- Initial supported currencies: PKR and CAD.
- Foreign-exchange gain/loss reporting is deferred unless required.

## 8.9 Period-close workflow

~~~text
Owner runs pre-close checks
→ unresolved exceptions shown
→ stock balances reconciled
→ unposted documents listed
→ Shopify/COD reconciliation checked
→ expense and ledger checks completed
→ inventory and channel snapshots generated
→ draft P&L generated
→ Owner reviews
→ FiscalPeriod set CLOSED
→ ProfitDeclaration approved
→ Partner allocations and CapitalEntries posted
→ optional final LOCK after external review
~~~

Hard blockers:

- Stock-balance reconciliation difference.
- Negative on-book balance.
- Failed posted-document integrity check.
- Unresolved movement missing an open-period mapping.

Warnings:

- Draft documents.
- Unmatched COD lines.
- Expiring inventory.
- Overdue receivables.
- Missing expense allocation.

## 8.10 UI requirements

- Expense categories and expense entry.
- Expense attachment preview.
- Cost-allocation form.
- Party ledger and aging.
- Supplier payable report.
- Revenue/COGS/contribution drill-down.
- Period-close checklist.
- Draft and approved P&L.
- Profit declaration and partner allocation.
- Capital ledger by equity partner.
- Current period cannot display a false “final” label before close.

## 8.11 APIs

| Method | Endpoint |
|---|---|
| GET/POST | `/api/v1/finance/expenses/` |
| POST | `/api/v1/finance/expenses/{id}/post/` |
| POST | `/api/v1/finance/expenses/{id}/reverse/` |
| GET | `/api/v1/finance/party-ledger/` |
| GET | `/api/v1/finance/aging/` |
| GET | `/api/v1/finance/pnl/` |
| POST | `/api/v1/finance/period-close/preview/` |
| POST | `/api/v1/finance/period-close/execute/` |
| GET/POST | `/api/v1/finance/profit-declarations/` |
| POST | `/api/v1/finance/profit-declarations/{id}/approve/` |
| GET | `/api/v1/finance/capital-ledger/` |

All general finance APIs are Owner-only. Distributor financial access continues through restricted Stage 6 endpoints.

## 8.12 Tests

- Revenue, COGS, gross profit and contribution calculations.
- Sell-in discount not double-counted as commission.
- Consignment COGS recognition.
- Refund and credit-note impact.
- COD recognition-policy test.
- Expense posting/reversal.
- Allocation sum validation.
- Multi-currency snapshot precision.
- Party balance and aging.
- Period pre-close blockers.
- Snapshot accuracy.
- Closed-period immutability.
- Profit allocation sum and capital entries.
- Distributor denial for profit/cost endpoints.

## 8.13 Definition of Done

- Every P&L number drills down to posted documents.
- Profit terminology is mathematically correct.
- Distributor and supplier balances reconcile to their ledgers.
- COGS uses snapshotted batch costs.
- Closed periods cannot be modified.
- Partner capital is derived from immutable entries.
- Owner can reproduce a closed period from snapshots and source documents.

---

# Stage 9 — Reporting, Alerts, Documents and Operational UX

## 9.1 Objective

Deliver operational dashboards, reports, exports, notifications, printable documents and consistent day-one user experience on top of trusted transaction data.

## 9.2 Django applications

- `reporting`
- `notifications`
- `audit`
- all read-only reporting integrations

## 9.3 Reporting requirements

### Inventory reports

- Stock on hand by company, product, batch and location.
- Available, reserved, quarantined, damaged, expired and in-transit quantities.
- Movement ledger.
- Batch traceability from receipt to dispatch/return.
- Reorder report.
- Out-of-stock report.
- Expiry at 90/60/30 days.
- Inventory valuation.
- Stock count variance.
- Consignment stock by distributor.
- Channel allocation versus physical availability.

### Procurement reports

- Purchase order status.
- Ordered versus received.
- Goods receipt and batch report.
- Purchase by supplier/manufacturer.
- Landed-cost breakdown.
- Supplier invoice and payment.
- Supplier payable aging.
- Purchase returns.

### Sales and distributor reports

- Sales by channel, product, distributor and period.
- Stock request requested versus approved.
- Order, dispatch and invoice status.
- Sell-in versus consignment.
- Sell-through and consignment reconciliation.
- Distributor outstanding and aging.
- Credit utilization.
- Distributor price/discount/commission history.
- Sales returns and credit notes.
- Free/bonus goods and associated COGS.

### Shopify and logistics reports

- Shopify order synchronization status.
- Unmapped variants.
- Failed webhook events.
- Published versus computed inventory.
- Fulfillment queue.
- Courier shipment status.
- COD expected versus collected versus remitted.
- Unmatched remittance lines.
- Courier charges and RTO.

### Finance reports

- Gross sales, discounts, returns and net revenue.
- COGS and gross profit.
- Variable selling costs and contribution margin.
- Operating expenses and operating profit.
- Profit by product, channel and distributor where allocation exists.
- Period P&L.
- Expense by category/cost center.
- Equity-partner allocation and capital ledger.
- Closed-period snapshots.

### Audit reports

- Master-data changes.
- Posted/reversed documents.
- Permission-sensitive actions.
- Override reasons.
- Login and account-deactivation events.
- Import and synchronization history.

## 9.4 Report behavior

Every report must:

- Apply company scope.
- Apply Distributor row scope where applicable.
- Show an as-of timestamp.
- Display active filters.
- Provide drill-down to contributing documents.
- Use server-side pagination for large datasets.
- Export CSV/XLSX where approved.
- Export PDF only for formatted reports/documents where PDF adds value.
- Preserve decimal precision.
- Avoid exposing hidden sensitive columns in exports.
- Identify whether data is live, cached or period-snapshotted.

## 9.5 Dashboard requirements

### Owner dashboard

- Sellable stock.
- Low-stock product count.
- Expiring batch count.
- Orders requiring approval.
- Shopify orders requiring fulfillment.
- In-transit transfers.
- Distributor outstanding and overdue.
- COD awaiting remittance.
- Current-period sales by channel.
- Current-period gross profit/contribution with “provisional” label until close.
- Failed background jobs/integration alerts.

Dashboard cards must link to filtered detail, not dead-end numbers.

### Distributor dashboard

- Own pending request count.
- Own order statuses.
- Own invoices due.
- Own outstanding balance.
- Own available credit.
- Own consignment stock.
- Own sell-through report due.
- Recent shipment tracking.

## 9.6 Notification models

### `AlertRule`

- `company`
- `rule_type`
- `product` nullable
- `location` nullable
- `party` nullable
- `threshold`
- `warning_days`
- `channels`
- `recipients`
- `active`

### `Alert`

- `company`
- `rule`
- `severity`
- `subject_type`
- `subject_id`
- `title`
- `message`
- `status`: `OPEN`, `ACKNOWLEDGED`, `RESOLVED`, `DISMISSED`
- `deduplication_key`
- `opened_at`
- `acknowledged_by`
- `resolved_at`

### `NotificationDelivery`

- `alert`
- `channel`: `IN_APP`, `EMAIL`, `WHATSAPP`
- `recipient`
- `status`
- `attempts`
- `sent_at`
- `error`

WhatsApp delivery depends on an approved provider and credentials; in-app and email must work without it.

## 9.7 Alert behavior

- Avoid duplicate open alerts using a deduplication key.
- Repeated evaluations update the existing open alert.
- Resolved condition automatically resolves eligible alerts.
- Manual dismissal requires a reason.
- Critical integration failures notify Owner only.
- Distributor receives only alerts related to their own records.

## 9.8 Printable documents

Generate branded printable versions of:

- Purchase order.
- Goods receipt note.
- Transfer note.
- Stock request.
- Dispatch note.
- Sales invoice.
- Receipt.
- Credit note.
- Return note.
- Distributor statement.
- Sell-through statement.

Every document must include:

- Company identity.
- Document number and status.
- Issue/posting date.
- Party and address.
- Line details.
- Currency and totals.
- Reference documents.
- Clear `DRAFT`, `POSTED`, `CANCELLED` or `REVERSED` marking.

## 9.9 UX requirements

- Phone-first inventory, receipt, dispatch and count pages.
- Keyboard-first desktop entry.
- Barcode scanner input supported as keyboard input.
- Forms preserve entered values after validation/server failure.
- Destructive/posting actions require consequence-focused confirmation.
- Every quantity and balance is drillable.
- Every quantity shows an as-of timestamp.
- Saved filters are available on major lists.
- Search supports SKU, barcode, product name, batch, document number and party.
- Status colors are paired with text; meaning cannot depend on color alone.
- Tables remain usable on mobile through responsive layouts.
- Empty states explain the next valid action.
- Permission denial returns a clear message without revealing hidden records.

## 9.10 Performance requirements

Initial targets under expected small-business load:

- Normal authenticated page response: p95 under 800 ms excluding third-party APIs.
- Simple API list/detail: p95 under 500 ms.
- Posting transaction: p95 under 2 seconds excluding file upload.
- Dashboard: p95 under 2 seconds using cached aggregates where needed.
- Large exports run asynchronously.
- Reports must not create N+1 query patterns.
- Add indexes for company, date, status, party, product, batch, location and document number filters.
- Use materialized views or summary tables only where measured performance justifies them.

## 9.11 Background schedule

| Task | Schedule |
|---|---|
| Check reorder points | Daily and after relevant commit |
| Check expiry | Daily |
| Check receivables | Daily |
| Expire stale reservations | Hourly |
| Pull Shopify orders | Every 30 minutes |
| Reconcile Shopify | Nightly |
| Check in-transit delays | Daily |
| Check COD remittance delays | Daily |
| Send queued notifications | Continuous/short interval |
| Generate scheduled summaries | Configurable |
| Rebuild/report balance reconciliation | Nightly or on demand |

## 9.12 Tests

- Report formula reconciliation.
- Filter, date-range and company-scope tests.
- Distributor report row isolation.
- Export column-leak tests.
- Alert deduplication and resolution.
- Notification retry behavior.
- Document totals and status watermark tests.
- Dashboard drill-down filters.
- Query-count tests on critical pages.
- Mobile viewport and keyboard-flow checks.
- Accessibility checks on core workflows.

## 9.13 Definition of Done

- Owner and Distributor dashboards contain only authorized data.
- Every displayed balance can be traced to source records.
- Reports reconcile with the underlying ledgers.
- Exports do not leak hidden fields.
- Alerts are actionable and not duplicated.
- Required documents print correctly.
- Critical pages meet agreed performance targets.

---

# Stage 10 — Migration, Security, QA, Deployment and Production Launch

## 10.1 Objective

Prove the entire system is secure, consistent and operable; migrate opening data; deploy production infrastructure; complete user acceptance testing; and launch with backups and recovery procedures.

## 10.2 Data migration scope

Prepare controlled imports for:

- Companies and settings.
- Owner accounts.
- Distributor parties, profiles and accounts.
- Manufacturers, suppliers, couriers and customers.
- Products, ingredients and channel prices.
- Locations.
- Opening batch stock by location and unit cost.
- Open purchase orders.
- Open sales/distributor orders where required.
- Distributor opening balances.
- Supplier opening balances.
- Open invoices and receipts.
- Equity-partner opening capital balances.
- Shopify product/variant mappings.

## 10.3 Migration rules

1. Every import has a unique import batch ID.
2. Dry-run mode validates without writing.
3. Import errors identify row and reason.
4. Re-running the same import is idempotent.
5. Opening inventory posts movements; it never writes balances directly.
6. Opening financial balances post ledger entries.
7. Source totals and imported totals must reconcile.
8. Owner signs off every reconciliation report.
9. Original source files are retained with access control.
10. Rollback occurs through documented reversal or pre-launch database restore, not ad hoc deletion.

## 10.4 Security requirements

### Application security

- HTTPS only in production.
- Secure, HttpOnly and SameSite cookies.
- CSRF protection on session-authenticated requests.
- Strong password hashing using supported Django defaults or stronger approved configuration.
- Login rate limiting.
- Password reset rate limiting.
- Session expiry and revocation.
- Strict role and row-level authorization.
- Separate Owner and Distributor serializers/views for sensitive resources.
- File upload type, size and malware-validation controls.
- HMAC verification for Shopify webhooks.
- Secrets encrypted at rest where stored and excluded from logs.
- Security headers through Django/Nginx.
- No detailed stack traces in production.

### Database security

- Application uses a non-superuser database account.
- Least-privilege database permissions.
- Production database is not publicly accessible.
- Encrypted connections.
- Automated backups.
- Restricted manual access with auditability.

### Data privacy

- Only necessary personal data is collected.
- Distributor cannot enumerate IDs to discover other records.
- Exports and file URLs require authorization.
- Signed file URLs expire.
- Logs avoid personal and financial payloads unless explicitly redacted.

## 10.5 Complete testing requirements

### Unit tests

- All calculations.
- All status transitions.
- Price/term resolution.
- Credit validation.
- Stock movement validation.
- Permission helpers.

### Integration tests

- Procurement receipt through inventory.
- Distributor request through payment.
- Consignment dispatch through sell-through.
- Shopify order through fulfillment/refund.
- Direct sale and return.
- COD shipment through remittance/RTO.
- Expense through P&L.
- Period close and capital allocation.

### Concurrency tests

- Two dispatches competing for the same stock.
- Transfer and sale competing for the same stock.
- Duplicate posting requests.
- Duplicate Shopify events.
- Concurrent document numbering.
- Concurrent inventory publication.

### Security tests

- Owner versus Distributor endpoint matrix.
- Cross-distributor object access.
- Hidden-field API responses and exports.
- CSRF/session behavior.
- File-access authorization.
- Token/log redaction.
- Common injection and unsafe-input checks.

### Recovery tests

- Restore database from backup.
- Restore object-storage documents.
- Rebuild stock balances.
- Recompute party balances.
- Replay failed Shopify events safely.
- Resume interrupted imports.

### User acceptance tests

Owner must successfully complete:

- Create master data.
- Receive a batch.
- Release stock.
- Transfer stock.
- Count and correct stock.
- Approve and dispatch sell-in order.
- Process consignment and sell-through.
- Fulfill Shopify order.
- Complete direct sale.
- Process return/RTO.
- Post expense and payment.
- Run reports.
- Close a period.

Distributor must successfully complete:

- Login and manage own profile.
- View own catalog and price.
- Submit stock request.
- View approval/order/dispatch.
- View invoice, receipt and ledger.
- Submit sell-through.
- Submit return.
- Fail to access another distributor's data.

## 10.6 Deployment architecture

Production services:

- Nginx.
- Gunicorn/Django web container.
- Celery worker.
- Celery beat scheduler.
- PostgreSQL.
- Redis.
- S3-compatible object storage.
- Monitoring/error tracking.
- Centralized logs.

Production requirements:

- Separate production and staging environments.
- Automated migration step before application rollout.
- Zero or controlled downtime deployment.
- Rollback plan for application version.
- Database migration rollback assessment for every release.
- Environment variables managed outside source control.
- Health checks used by deployment platform.
- Static assets versioned.

## 10.7 Backup and disaster recovery

- Automated daily database backups.
- Point-in-time recovery when hosting supports it.
- Backup retention policy approved by Owner.
- Object-storage versioning or backup.
- Monthly restore test initially, then quarterly after stability.
- Written recovery runbook.
- Recovery objectives documented:
  - RPO: maximum acceptable data loss.
  - RTO: maximum acceptable restoration time.

No production launch until at least one successful restore test is documented.

## 10.8 Monitoring and operations

Monitor:

- Web/API error rate.
- Request latency.
- Database connections and slow queries.
- Celery queue depth and failures.
- Shopify webhook failure rate.
- Shopify reconciliation drift.
- Stock-balance reconciliation differences.
- Backup success.
- Disk/storage capacity.
- Authentication failures.

Create runbooks for:

- Shopify token failure.
- Failed webhook backlog.
- Negative/reconciliation inventory incident.
- Incorrect posted document.
- Distributor access issue.
- Database outage.
- Restore from backup.
- Emergency user deactivation.

## 10.9 Production launch plan

### Before launch

- Freeze source spreadsheets.
- Complete final import dry run.
- Complete UAT.
- Resolve critical/high defects.
- Confirm tax and revenue-recognition policy with accountant.
- Confirm free-goods policy.
- Confirm opening balances.
- Confirm Shopify scopes and variant mappings.
- Confirm courier/COD format.
- Verify backups and restore.
- Train Owner and pilot Distributor.

### Launch

- Enable production read access.
- Import final opening data.
- Reconcile stock and balances.
- Enable posting.
- Enable Shopify webhooks and polling.
- Publish verified Shopify inventory.
- Invite approved Distributors.
- Monitor first transactions directly.

### After launch

- Daily reconciliation for first two weeks.
- Review failed jobs every day.
- Compare physical stock with system sample counts.
- Compare Shopify orders and availability.
- Compare invoices, receipts and distributor statements.
- Hold a 7-day and 30-day launch review.

## 10.10 Release gates

Production release is blocked by:

- Critical security defect.
- Inventory reconciliation difference.
- Cross-distributor data leak.
- Failed backup restore.
- Duplicate posting under retry/concurrency.
- Unconfirmed opening balances.
- Shopify mapping errors for active products.
- Incorrect P&L formula.

## 10.11 Definition of Done

- Opening data is imported and signed off.
- All critical workflows pass UAT.
- Permission and data-isolation tests pass.
- Concurrency and idempotency tests pass.
- Backup restoration succeeds.
- Production monitoring and runbooks exist.
- Owner approves final reconciliation.
- System is deployed and operating with only Owner and Distributor roles.

---

# Appendix A — Stage Dependency and Release Matrix

| Stage | Must depend on | Primary release outcome |
|---|---|---|
| 1 | None | Working Django engineering baseline |
| 2 | 1 | Company, users, roles, periods and scoping |
| 3 | 2 | Parties, products, prices and locations |
| 4 | 3 | Trusted inventory ledger |
| 5 | 4 | Procurement and landed-cost receipts |
| 6 | 4, 5 | Distributor sales, portal and receivables |
| 7 | 4, 6 | Shopify, direct sales, fulfillment and COD |
| 8 | 5, 6, 7 | Management finance and period close |
| 9 | 4–8 | Dashboards, reports, alerts and documents |
| 10 | 1–9 | Secure production launch |

No stage may be marked complete only because models exist. Its workflows, permissions, tests and Definition of Done must also pass.

---

# Appendix B — Final Permission Matrix

| Capability | Owner | Distributor |
|---|---:|---:|
| Company settings | Full | None |
| User management | Full | Own password/profile only |
| Parties | Full | Own Party only |
| Products | Full | Eligible catalog read-only |
| Manufacturer/supplier data | Full | None |
| Channel prices | Full | Resolved own price only |
| Product cost/landed cost | Full | None |
| Locations | Full | Own consignment summary only |
| Inventory balances | Full | Restricted availability/own consignment |
| Inventory movements | Full | None |
| Procurement | Full | None |
| Distributor agreements | Full | Own commercial result only |
| Stock requests | All | Own |
| Sales orders | All | Own |
| Dispatches/shipments | All | Own |
| Invoices/receipts/credits | All | Own |
| Sell-through | All | Own create/view |
| Returns | All | Own request/view |
| Shopify | Full | None |
| Expenses/P&L | Full | None |
| Party ledger | All | Own statement |
| Equity capital | Full | None |
| Fiscal-period close | Full | None |
| Audit log | Full | None |
| Reports | All | Own restricted reports |

---

# Appendix C — Global Acceptance Criteria

The completed platform is accepted only when all statements below are true:

1. Every unit of stock can be traced from supplier receipt to its current location or final exit.
2. Every displayed stock balance can be rebuilt from immutable movements.
3. Every Distributor sees only their own commercial and transaction data.
4. Owner can see complete operational and financial data.
5. Shopify, direct and distributor sales use the same inventory truth.
6. Orders reserve stock; only dispatch or sell-through moves stock.
7. Batch expiry, hold and recall rules prevent invalid sales.
8. Sell-in discount and consignment commission are not double-counted.
9. Distributor outstanding is derived from immutable ledger entries.
10. Closed periods cannot be modified.
11. Returns, RTOs, refunds and write-offs preserve both stock and financial traceability.
12. Reports reconcile to operational documents and ledgers.
13. Duplicate requests/events cannot create duplicate stock or money entries.
14. Production backups can be restored.
15. Only Owner and Distributor are authenticated business roles.

---

# Appendix D — Decisions Required Before Production

The following must be confirmed by the Owner and relevant professional adviser before production:

1. Pakistan tax rates and invoice requirements for the product category.
2. Withholding treatment on distributor and courier payments.
3. Final revenue-recognition event for sell-in, Shopify prepaid and Shopify COD.
4. Whether Owner may override distributor credit limits.
5. Free-goods/bonus scheme rules such as `13+1`.
6. Return eligibility windows and near-expiry return policy.
7. Default FEFO warning window.
8. Reorder levels by product/location.
9. Shopify inventory automatic-decrease/manual-increase policy.
10. Courier remittance file formats.
11. Opening stock, receivables, payables and equity capital.
12. Backup retention, RPO and RTO.
---

# Appendix E — Recommended Implementation Rule

Stages must be implemented sequentially. Stage 4—the inventory ledger—is the central dependency and must receive the strongest review, concurrency testing and reconciliation testing. Financial reporting must not be trusted or released until inventory, sales, returns and courier flows are reconciled.

The first production pilot should use:

- One company.
- One Owner account.
- One approved Distributor account.
- A small controlled product set.
- One owned warehouse.
- One Shopify store.
- One courier/COD format.

Expand to all products and distributors only after the pilot balances reconcile.
