# Current state

Updated: 2026-11-01

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
