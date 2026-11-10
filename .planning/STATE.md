# Current state

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
