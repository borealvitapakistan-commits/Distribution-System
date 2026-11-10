# Readable category workbooks

Completed: 2026-11-10

## Result

Settings exports and imports .xlsx Excel workbooks instead of exposing JSON-packed CSV rows. Every persistent model category has a named sheet with S.No., individual field columns, stable Record ID, and readable names beside foreign-key IDs. Product Name and Description are placed first. Headers, filters, frozen rows/columns, widths, wrapping, field guidance comments and choice dropdowns make the workbook usable in Excel.

Users may edit existing values or add new rows. New IDs/defaults/timestamps are generated where supported; new products receive SKU/handle defaults. Unique linked names can resolve new FK links when the ID cell is blank. Explicit IDs remain authoritative. Implicit many-to-many links use separate relationship sheets. Scalar values, required fields, choices, related IDs, schema, headers and attachment integrity are checked before replacement. Product descriptions use the existing HTML sanitizer.

Date/time and exact decimal strings remain lossless. Very large integers are written as text. Long/control-character text and binary fields use separate numbered parts to avoid Excel's cell limits. Files contains complete external and database-backed attachments with part counts and SHA256 checks. Formula-like exported user text is literal; imported formulas are rejected. Workbook expansion and cell counts are bounded.

Dated replacement backups now use .xlsx and retain the existing private filesystem/S3 storage and transaction/rollback behavior. The internal legacy CSV parser remains available for old backup compatibility. The Settings upload accepts .xlsx. Import now refuses pending database migrations before backup/deletion with an explicit message.

## Local-data artifacts

- Primary workbook: `data/backups/exports/local-data-category-sheets-20261110-125742.xlsx`.
- Adapted the original local SQLite export to the current owner-only schema in an offline process. Contains 1,142 importable records, 18 products, 3 manufacturers, 2 purchase orders, 1 brand, a database-backed logo and 2 ordinary file attachments.
- Retired distributor user/profile records remain in separate Archive sheets, along with any removed links. Archive sheets are documented as reference-only and are excluded from owner-only imports. No retired records were silently discarded.
- Complete original-schema workbook: `data/backups/exports/local-data-original-categories-20261110-125740.xlsx`; retains all 1,142 original records and 3 original attachments in their original categories. This separate archival workbook matches the original app schema, not the current owner-only schema.
- Source CSV and original local database remain unchanged. Private artifacts are mode 0600, under ignored data/backups and never committed.
- Primary workbook was parsed and fully restored into a fresh isolated in-memory SQLite database. All 18 products, brand/logo and relationships restored successfully. No Supabase import was performed.

## Verification

- Full SQLite suite: 359 passed.
- Full isolated PostgreSQL 17 suite: 359 passed.
- 19 transfer tests include unchanged round trips, M2M/FK links, files/logos, new/edited rows, named links, generated product defaults, valid HTML sanitization, large text/binary data, exact values, literal formula-like strings, invalid formulas/headers/parts/fields, pending migrations, backups, permissions and rollback.
- Browser on isolated sample server: .xlsx download, upload of that same workbook, replacement acknowledgement, dated .xlsx backup, successful replacement and logout.
- Django system check, migration drift and whitespace checks passed.
- Main Supabase-connected app restarted at localhost:8001 (session 82904) and health returned HTTP 200 / status ok. Sample server and temporary PostgreSQL test server stopped.
- Supabase core.0007_remove_brand_logo_data remains pending, as before. No live migration or import was performed; import will explicitly refuse until the schema update is completed.

## Commits

- `04f32a8` — readable/editable workbook backend and dependency.
- `52630b6` — Settings/endpoints and transfer tests.
