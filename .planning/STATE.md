# Current state

Updated: 2026-11-10 — One business workbook / one upload

- User clarified they want a SINGLE .xlsx with multiple business-section sheets, imported together. Individual CSVs and full backup/system datasets were not the intended deliverable.
- Completed `phases/01-partners/01-11-PLAN.md`. Settings now downloads only business records across 28 category sheets; stable IDs/support data hidden. Users/passwords/permissions/audit/cache/registry sheets are excluded. Business imports preserve existing login accounts, passwords, permissions and history while the app saves a complete private dated backup automatically.
- Delivered file: `data/backups/exports/local-business-data-20261110-132818.xlsx` — 117 business rows, 18 products, 3 manufacturers, 2 orders, 15 items, 16 sizes/prices, 3 attachments. A complete isolated import with a different current owner login succeeded and preserved that login/password.
- Full suites: 364 passed on SQLite and 364 on PostgreSQL; 24 transfer tests. Checks passed. Implementation: `dbd0f3e`, `5000d5a`. No live import/migration. Existing Supabase core.0007 still blocks live imports until applied; this feature needs no new migration. Main app restarted at localhost:8001. See `phases/01-partners/01-11-SUMMARY.md`.

Updated: 2026-11-10 — Readable category workbooks

- Completed `phases/01-partners/01-10-PLAN.md`: Settings now exports/imports .xlsx workbooks, with named category sheets, S.No., separate field columns, readable linked names, field guidance/dropdowns and safe complete attachments. Users can edit rows or add new rows with generated IDs/defaults. Old CSV backups remain internally readable.
- Implemented validation of scalar fields/choices, relations, required sheets/headers, formulas and multipart attachments; product descriptions use the existing sanitizer. Import refuses pending migrations before any backup/deletion. Dated backups now use .xlsx.
- Delivered local-data workbook: `data/backups/exports/local-data-category-sheets-20261110-125742.xlsx`. Contains current-schema local records (18 products, 3 manufacturers, 2 orders) plus reference-only Archive sheets preserving retired distributor user/profile records. Complete original-schema workbook also retained at `data/backups/exports/local-data-original-categories-20261110-125740.xlsx`. Both private/ignored; source data unchanged.
- Primary workbook fully restored in an isolated database. Full suite: 359 passed on SQLite and 359 on local PostgreSQL; 19 transfer tests. Browser XLSX download/import/dated backup succeeded on sample data. System/migration/whitespace checks passed.
- Implementation commits: `04f32a8`, `52630b6`. Main Supabase app restarted at localhost:8001, health OK (session 82904). Temporary test servers stopped. No live migration/import; core.0007 remains pending and blocks live imports until completed. See `phases/01-partners/01-10-SUMMARY.md`.

Updated: 2026-11-10 — Supabase runtime connected

- User requested switching the running local app from sample data to Supabase. Saved supplied database/private-storage credentials only in ignored .env (mode 0600). Enabled private S3 storage after successful read-only bucket check.
- Read-only schema check: PostgreSQL reachable, all current model tables/columns present, 1 active owner, 2 brands, 12 products. Pending migration: core.0007_remove_brand_logo_data (removes legacy logo columns); not applied because it alters the shared database and is not necessary for connection verification.
- Stopped the sample-data server. Normal config.settings app is running at http://127.0.0.1:8001 against Supabase. Health returned status ok / HTTP 200; sign-in page returned HTTP 200. Use an existing Supabase owner account, not the temporary preview credentials.
- No production migration, data import or storage write performed during connection verification. The pending migration remains relevant to full write/import compatibility. Runtime session at completion: 36630.

Updated: 2026-11-10 — GitHub integration

- Completed `phases/01-partners/01-09-PLAN.md`: fetched origin/main `07d79f7` and merged its five newer commits with all local export/import work. Integration commit: `1e177e4`.
- Upstream now uses owner-only roles (distributor apps removed), PostgreSQL/Supabase, private S3 uploads, Vercel, database logos, new UI and caches. Preserved these changes.
- Adapted transfer for PostgreSQL table locks/FKs/sequences, database and legacy logos, durable hosted backups, cache-version refresh, excluded transfer-page caching and new panel/menu layout.
- Verification: full suite 352 passed on SQLite and 352 passed on isolated local PostgreSQL 17. Browser CSV download → upload → dated backup → replacement → logout → restored-account login passed; desktop/mobile inspected. Final 12 transfer tests and system/migration/whitespace checks passed.
- Original local DB, .env and live Supabase/S3 were untouched. No push/deployment. S3 routing tested through isolated storage, not the live service. See `phases/01-partners/01-09-SUMMARY.md` for runtime boundaries.
- Temporary sample-data preview is running at localhost:8001 using /tmp/distribution_integration_preview_settings.py. Temporary PostgreSQL test server has been stopped. Normal app settings still require a configured DATABASE_URL and appropriate private-storage variables; these were not changed during integration.

Updated: 2026-11-10

- Completed `phases/01-partners/01-08-PLAN.md`: Settings > Export & Import Data exports a single CSV with all persistent records, relationships, accounts and referenced attachments. Sessions/API tokens are excluded.
- Import validates the unchanged CSV, requires replacement acknowledgement, saves a private dated backup to `data/backups/data-imports/`, then atomically replaces current records and uploads. Errors preserve previous data. Existing backups remain. Successful imports require sign-in with imported credentials.
- Backup browsing is deferred at the user's request; backup contents are already retained. No migrations needed.
- Validation: full suite 403 passed (8 transfer tests); actual-data round trip in an isolated temporary SQLite copy preserved all 1,142 records and 3 attachments. Real data unchanged. System/migration/whitespace checks passed.
- Implementation commits: `ecae692`, `1d1af38`. See `phases/01-partners/01-08-SUMMARY.md`.

Updated: 2026-11-01

- Removed the "Order from [manufacturer]" shortcut from product pricing cards at the user's request. Commit: `74395d3`.
- Removed the Bottle sidebar section from the product editor as requested. Main/additional media controls remain the image entry points. Two label/media tests passed; previously saved bottle files are preserved. Commit: `ad20054`.
- User clarification: only one Save button, at the bottom. Removed the top Save; bottom still saves all details and manufacturer prices. Five unified-save tests passed, including exactly one submit button. Commit: `1eddea9`. Supersedes the two-button UI described below.
- Product Save is now unified: top/bottom buttons both read Save and atomically save product details, ingredients, media, allocations and all displayed manufacturer sizes/prices. Per-manufacturer save buttons removed; bottom Save follows all pricing cards. Success stays in the editor with a bottom toast: "Product has been saved."
- Unified-save validation: all 75 product tests pass; browser verified both buttons save details and prices, confirmation positioning, Back navigation and mobile width. Invalid pricing rolls back all database edits and keeps typed prices visible. Commit: `7fc4706`; see `phases/01-partners/01-07-SUMMARY.md`.
- Simplified product editor: seven selectable types with automatic measurement, optional collapsed label claim with ingredient/quantity/medicinal rows, no general size/price/shipping/extra-detail inputs or SKU entry. Barcode remains optional. Manufacturer size labels follow type; retail input is fixed and independent of cost.
- Existing hidden product and ingredient metadata is preserved. Legacy types remain stored until changed. Fixed existing ingredient-list editing uniqueness validation.
- Validation: full suite 390 passed; browser creation without SKU, seven measurement mappings, ingredient add/save, independent price saving and desktop/mobile layouts passed in an isolated database. Migration products `0015` applied locally after `data/backups/product-editor-20261101.sqlite3` backup. Implementation: `e3ed415`; details in `phases/01-partners/01-06-SUMMARY.md`.
- Removed duplicated PO prefixes from PDF downloads, ZIP filenames and contained advance/balance PDFs, including team copies. Download/document tests passed (12 tests). Commit: `3a901e5`.
- Follow-up screenshot showed baseline whitespace still made text appear high. Balanced top/bottom item-cell padding for xhtml2pdf's visible text, preserving total row padding; regenerated and visually inspected PO-SHC-001, still one page. Commit: `9249abb`.
- PDF product-row text now sits vertically in the middle, with descriptions left-aligned and numeric columns retaining their right alignment. Regenerated PO-SHC-001 advance PDF still fits one page. Commit: `2d23b7b`.
- Purchase order PDFs now try tighter row spacing before adding a page; forced breaks use rendered stamp space instead of always carrying the last two rows.
- Verified local PO-SHC-001 advance PDF fits all six products, terms, payment statement, totals and stamp on one page; inspected the rendered PDF preview.
- Validation: all 42 purchase-order tests passed, including six-product regression and longer-order final-page checks. Implementation commit: `3e1dba7`.

Updated: 2026-10-29

- Corrected local PO-GL-001's saved advance percentage from 0% to 30% to match its existing 30% advance / 70% balance terms. The existing PDF split logic now applies; no application code change was needed.
- Database backed up to `data/backups/po-payment-split-20261029.sqlite3`; correction recorded in the audit log.
- Verified actual PO through authenticated PDF endpoints: print/view and download each contain separate advance and balance sections (two pages each for this order); individual downloads and ZIP contain the correct 30% / Rs 685,950.00 and 70% / Rs 1,600,550.00 sections. Download chooser is enabled.

Updated: 2026-10-23

- Completed plan: `phases/01-partners/01-04-PLAN.md`.
- Products now use the supplied Shopify references: a focused catalog, directly editable product pages, real stock by location, media/description editing, bulk status actions, and confirmed delete/archive.
- Latest product validation: full suite 314 passed; final targeted workspace suite 14 passed; desktop/mobile browser flows passed in an isolated database.
- Removed manufacturer preset payment-before-shipping terms from forms/admin and order advance suggestions; per-order advance entry starts blank.
- Latest validation: 24 manufacturer/order/payment-flow tests passed; rendered screens and migration drift checks passed.
- Partners sidebar navigation is complete. Manufacturer deletion now reviews linked products with per-product reassign, hold, or delete choices.
- User decision: archive records with history, preserving orders, stock, and sales.
- Migrations: manufacturer `0017` and product `0011` archive timestamps applied locally after backup.
- Validation: all 300 Django tests passed; desktop/mobile browser checks and all four deletion outcomes passed using an isolated database copy.
- Existing manufacturers and their linked records were preserved.
- Local development server: http://127.0.0.1:8000 (session uses DEBUG=True).
- No outstanding work for this request.
