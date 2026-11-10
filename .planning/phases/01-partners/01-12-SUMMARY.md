# Supabase logo-column migration completed

Completed: 2026-11-10

## Changes

User explicitly authorized suitable changes and application of pending `core.0007_remove_brand_logo_data`.

Added a preservation step to the pending migration: copy missing/empty BrandLogo bytes from legacy columns before removing them, without overwriting newer nonempty logo records. Reverse migration repopulates recreated legacy fields from BrandLogo. PostgreSQL deferred FK events are flushed before the ALTER TABLE operations. Added forward/reverse regression coverage for missing, empty and newer logo data.

Implementation commit: `08e20ca`.

## Backup before applying

Created a complete PostgreSQL custom-format dump of the public schema, including business/account records, migration state and the original legacy logo columns. Used environment-only database credentials; secrets are not in command arguments or committed artifacts.

- Local private backup: `data/backups/logo-column-migration/before-logo-columns-20261110-135804-f77dd29e0ab7409ea369cac5e2913b9d.dump`.
- Hosted private backup: `data-backups/before-logo-columns-20261110-135804-f77dd29e0ab7409ea369cac5e2913b9d.dump` in configured Supabase storage.
- Size: 1,090,439 bytes. pg_restore archive listing verified; hosted readback SHA256 matched the local dump.
- Private verification manifest: `data/backups/logo-column-migration/latest.json` (0600). Database backup is also 0600; backup directory is private and ignored by Git.

## Live result

Applied only `core.0007_remove_brand_logo_data` successfully to Supabase PostgreSQL 17. No pending migrations remain. Old logo_data/logo_content_type columns are absent; existing served logo bytes/content type are preserved.

Compared pre/post logo hashes and counts for accounts, brands/logos, products, manufacturers, orders/items, product package prices, inventories, locations and stock. Business/account counts unchanged, including 12 products, 2 brands, 5 manufacturers, 38 purchase orders, 51 order items, 112 stock batches and 125 stock movements. Existing single BrandLogo record is preserved. No legacy logo required recovery in this database.

The actual local-business workbook (`local-business-data-20261110-132818.xlsx`, 117 business rows) passes format parsing and the live database migration preflight. No workbook import or replacement of Supabase business data was performed.

## Validation

- Full isolated PostgreSQL suite: 365 passed, including the migration forward/reverse test.
- Isolated SQLite migration test passed.
- System/migration drift and whitespace checks passed.
- Live app health HTTP 200 / status ok.
- Live public logo endpoint returned HTTP 200 with the expected SHA256.
- Temporary PostgreSQL test server stopped. Supabase-connected app remains at localhost:8001.
