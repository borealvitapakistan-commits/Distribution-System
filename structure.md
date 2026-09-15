# Core Structure — Distribution System

This document is the source of truth for how this system actually works. It replaces
[requirements.md](requirements.md) / [requirements-2.md](requirements-2.md) as the reference
for the core business shape — those files describe a much larger multi-tenant pharma-distribution
ERP (Shopify sync, COD/courier logistics, batch/expiry tracking, landed cost, equity partners,
fiscal-period locking, multi-company scoping) that was ChatGPT-generated and drifted away from
what this project actually needs. Keep this file updated as the real design evolves.

## 0. Single tenant

This system runs for **one brand**, not a multi-company platform. Nothing in the codebase is
scoped by "which company/tenant does this belong to" — that whole dimension was removed. Every
model that used to carry a `company` foreign key (`User`, `OwnerProfile`, `DistributorProfile`,
`Manufacturer`, `Customer`, `Product`, `ProductCategory`, `Ingredient`, `Location`,
`AuditEvent`) no longer has one, and per-company uniqueness constraints (e.g. "SKU unique per
company") are now plain global constraints. `apps/core.Brand` (renamed from `Company`) is kept
only as a standalone info record — name, logo, currency, address — with **no relationships to
anything else**; it's effectively a singleton settings row, surfaced in every template via a
context processor (`apps/core/context_processors.py:brand`) rather than through the logged-in
user.

`FiscalPeriod`, `DocumentSequence`, and `EquityPartner` — which only existed to be scoped
per-company for multi-tenant fiscal/equity tracking — were removed entirely along with their
views, forms, API endpoints and nav links.

## 1. Roles

Only two authenticated roles:

- **Owner** — runs the brand. Can be more than one Owner account. Owner logins double as
  Django admin logins (same row: `is_staff=True`, `is_superuser=True`). The root Owner created
  via `createsuperuser` now needs nothing else — no company/brand setup step required first.
- **Distributor** — buys stock from the Owner and resells it. Can be more than one
  Distributor account.

There is no separate "Sub-Distributor" role. If a Distributor sells on to someone who is
themselves reselling, that person is recorded the same as any other **Customer** — the system
does not track anything past the Distributor → Customer sale.

**Customer** and **Manufacturer** are not logins/roles — they're business records only
(who bought something / who the Owner buys stock from).

## 2. Relationships

```
Manufacturer ──supplies──► Owner ──sells──► Customer
                             │
                             └──sells (supplies stock)──► Distributor ──sells──► Customer
```

- The Owner buys finished stock from a Manufacturer.
- Owner can sell directly to a Customer.
- Owner can supply stock to a Distributor.
- A Distributor can only sell what the Owner has actually given them — a Distributor has no
  stock of their own until the Owner supplies it. This is a hard dependency, not just a
  suggestion: Distributor capacity to sell is gated by what they've received from the Owner.
- A Distributor sells on to Customers. What that Customer does afterward (resells it, keeps it,
  etc.) is out of scope — not tracked as a further tier.

## 3. Commission

- When the Owner adds a Distributor, the Owner sets a **commission percentage** on that
  Distributor at creation time (`DistributorProfile.commission_percentage`).
- When the Owner earns money from a sale involving that Distributor, the commission is
  calculated from that percentage and owed to the Distributor.
- That commission amount gets recorded into a **Finance view** (`apps/finance`) on the Owner's
  dashboard. Real Finance functionality (actual commission ledger entries, payouts) is not
  built yet — only an empty placeholder page exists so the navigation and structure are in
  place.

## 4. Scope for now

Keep the build lean. Everything below is explicitly **not** in scope until asked for again:

- Shopify sync
- COD / courier logistics
- Batch / expiry tracking
- Landed cost allocation
- Multi-tenant/multi-company scoping, equity partners, fiscal-period locking (removed — see §0)

## 5. App layout

One Django app per real-world entity, plus the shared/infra apps:

- `apps/accounts` — `User` model only (shared login for both roles), auth mechanics
  (login/logout/password reset), `RoleRequiredMixin`/`OwnerRequiredMixin`/
  `DistributorRequiredMixin`, `IsOwner`/`IsDistributor` permissions, and the shared helpers
  `require_owner`, `sync_user_group`, `invalidate_user_sessions`. Nothing role-specific lives
  here. `require_owner` is the single canonical version project-wide — every app's services
  import it from here.
- `apps/owners` — `OwnerProfile` (business info for an Owner account — name/phone/notes).
  Owner list, add-another-owner, self-service profile edit, deactivate. No approval step
  (an Owner adding another Owner is trusted immediately). Note: the root Owner created via
  `createsuperuser` has no `OwnerProfile` row — Owner-facing pages are keyed off `User`, not
  the profile, so that account still shows up and can self-edit.
- `apps/distributors` — `DistributorProfile` (name, phone, address, territory,
  `commission_percentage`, approval status), the single "Owner adds a Distributor" action
  (`create_distributor`, creates the login + profile together), approve/suspend/reject,
  self-service profile edit.
- `apps/customers` — `Customer` model + CRUD. Name is globally unique.
- `apps/manufacturers` — `Manufacturer` model + CRUD. Name is globally unique.
- `apps/finance` — placeholder Owner-only page, no models yet.
- `apps/catalog` — `Product`, `ProductCategory`, `Ingredient` (no Shopify/channel pricing —
  `Product.base_retail_price` is the single price). SKU/barcode/ingredient name globally unique.
- `apps/warehouse` — `Location` (`OWN`/`SUPPLIER`/`DISTRIBUTOR`/`CUSTOMER` types only, linked
  to `Manufacturer`/`DistributorProfile`/`Customer` respectively; no batch/quarantine/
  consignment/Shopify-allocation machinery). Location code globally unique.
- `apps/audit` — `AuditEvent`, used by every service function above.
- `apps/core` — `Brand` (see §0) plus the base abstract models (`UUIDModel`,
  `TimeStampedModel`, `AuditedModel`) and the shared `OwnerManagedQuerySet` (role-only access
  check, reused by most other apps' querysets).

There is no `apps/parties` and no generic `Party` model — every entity above is a concrete,
standalone model in its own app.
