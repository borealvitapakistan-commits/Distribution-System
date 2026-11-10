# One workbook containing business sections

Completed: 2026-11-10

The user clarified that the deliverable is one Excel workbook with separate business-section sheets and a single upload, rather than individual CSVs or a user-facing full-system backup.

## Behavior

- Settings downloads a business-only workbook with 28 category sheets. No Users, password hashes, permissions, audit history, registry, cache or database-logo tables are included. Business names and fields are visible; stable record/FK identifiers and attachment/schema support sheets are hidden.
- Products, manufacturers, purchase orders/items/payments/revisions, sizes/prices, inventories, locations, batches, stock, sales, expenses and related business categories are imported together through one .xlsx upload.
- The app still saves a complete dated private backup automatically before replacement. It reads preserved system/account records from that locked backup state and combines them with the imported business rows for the transactional restore.
- Current login accounts, password hashes, permission groups and history remain intact even when they changed after the business workbook was exported. Owner business profiles map to existing login accounts, rather than importing credentials. The requesting owner is used as actor for new records. Existing creation metadata is preserved where available; update timestamps/actors reflect the import.
- Database logos are carried as normal embedded attachments in the business workbook and recreated as BrandLogo records on import. New IDs, defaults and linked-name handling remain supported. Order/revision creation dates are included as business dates.
- Settings rejects private full-system backup files as business uploads. The original full snapshot/restore service remains for internal backup compatibility.
- Previous protections remain: complete schema/header/field/attachment validation, pending-migration preflight, transaction rollback, FK checks, sequence reset and cache refresh.

## Delivered artifact

`data/backups/exports/local-business-data-20261110-132818.xlsx`

Contains 117 local business records across 28 sheets and 3 attachments, including 18 products, 3 manufacturers, 2 purchase orders, 15 purchase-order items and 16 manufacturer sizes/prices. Includes only current software business sections; previous archival/full files remain preserved privately. File is mode 0600 and ignored by Git.

Verified an entire one-file restore in a fresh isolated database with a DIFFERENT owner login account: all 18 products, 3 manufacturers, 2 orders, 15 items, 16 prices and brand logo restored; the existing test account/password remained intact. This verifies the file is an importable business workbook, rather than a presentation-only conversion.

## Validation and runtime

- Full SQLite suite: 364 passed.
- Full isolated PostgreSQL suite: 364 passed.
- 24 transfer tests cover both full snapshots and business imports, including account/password/group/history preservation, new named product links, owner-profile association, hidden identifiers, omitted system categories, full-backup upload rejection and automatic complete backups.
- System, migration drift and whitespace checks passed. No new database migration required for this feature.
- Supabase live data was NOT imported or migrated. Existing pending core.0007 remains a prerequisite for live imports; the importer rejects it before backup/deletion. Main app restarted at localhost:8001; temporary PostgreSQL test server stopped.

## Commits

- `dbd0f3e` — business workbook and preservation during one-file import.
- `5000d5a` — Settings and tests.
