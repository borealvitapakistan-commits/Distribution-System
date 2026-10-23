# Product workspace — complete

## Changes
- Rebuilt the catalog around the supplied reference: product thumbnails/names, status, and real available inventory; added search, status tabs, inventory sorting, 50-row pagination, selection, bulk activation/hold, and reviewed deletion.
- Clicking a product opens its directly editable page. Create and edit share the same grouped layout: title, formatted description, media, category, pricing, inventory by location, shipping, supplement details, ingredients, and additional fields.
- Kept existing package-size and bottle-price tools in an expandable section. Import/export and price sheets remain available.
- Added media upload previews, main-image replacement/removal, gallery removal, required-field disclosure handling, and unsaved-change protection.
- Description formatting is sanitized before preview/save. Formatting, lists, and HTML source mode work without adding an external editor dependency.
- Inventory figures use existing warehouse balances and the same sellable-location rules as the inventory sync. In-transit stock is shown explicitly instead of fabricating a committed/reserved quantity. Add stock preselects the product; movement history is filtered to it.
- Owner-only lifecycle actions require review/confirmation for deletion and preserve trading history by archiving. Unused products can be permanently removed. Audit events are recorded.

## Validation
- Full Django suite: 314 tests passed.
- Final targeted workspace suite after clearing imported image fallback metadata: 14 tests passed.
- Playwright against an isolated database copy verified search, selection counts, product creation, title/price/description saving, formatting, sanitized HTML, gallery uploads, main-image upload/removal, bulk reactivation, permanent deletion, archival with preserved stock, preselected Add stock links, and pagination.
- Inspected desktop and 390px mobile screenshots; catalog/editor fit the viewport and inventory tables scroll inside their cards.
- Verified archived fixture still retained two stock balances, two immutable movements, and its gallery. Browser actions did not modify the user's original database or call Shopify.
- Django system checks, migration drift, JavaScript syntax, and whitespace checks passed. No schema migration required.

## Commits
- `21bd9a0`: inventory summaries and reviewed product lifecycle actions.
- `a6f7a90`: simplified catalog and unified product editor.
- `23096a6`: media removal regression fix and responsive upload controls.

## Deviations and fixes
- Shared the existing manufacturer product-history inspection with standalone product lifecycle actions.
- Fixed a pre-existing clear-image failure: Django's clear checkbox supplies False, while the product service needs an empty file name. Clearing also removes the imported image fallback so the removed photo does not reappear.
- Corrected file-input positioning discovered during mobile checks.
- Preserved validation errors from both the main form and ingredient formset by validating them independently.

## Scope
Uses existing product and inventory data. Vendor, product type, channel, and catalog columns are omitted as requested. Unsupported Shopify metafield/variant/shipping-package features were not fabricated.
