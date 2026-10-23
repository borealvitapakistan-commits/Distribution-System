# Manufacturer deletion review — complete

## Changes
- Delete opens a review page listing directly linked products, supplier package sizes/prices, other suppliers, and retained order/warehouse history.
- Each product requires an explicit choice: reassign to an active manufacturer, put on hold, or delete.
- Reassignment transfers supplier prices; an existing replacement price for the same package size takes priority.
- Hold removes the old supplier's links/prices and marks the product inactive. Owners can find and reactivate held products under Products.
- Delete physically removes unused products. Products with history are archived, keeping their original supplier and catalog metadata.
- Manufacturers with remaining historical references are archived and hidden from lists/API; their detail page remains readable from historical orders.
- Added archive timestamps, transactional resolution, owner/CSRF enforcement, signed review tokens, stale-review detection, mandatory confirmation, and per-product/manufacturer audit events.
- New orders cannot use held/archived products or archived manufacturers.

## User decision
Preserve order, stock, and sales history; archive affected records instead of deleting history. Confirmed explicitly on 2026-10-23.

## Validation
- Full Django suite: 300 tests passed.
- Final targeted suite after the history-label copy adjustment: 30 tests passed.
- Playwright exercised four different product outcomes in one confirmation against an isolated copy of the database: reassign, hold, archive with order history, and permanent deletion of an unused product.
- Verified resulting database records, preserved order links, mobile layout, conditional replacement fields, and required confirmation.
- Original database still contains all three manufacturers, with no archived manufacturers/products from verification.
- Schema migrations applied locally after a SQLite backup. Migration drift, Django system checks, JavaScript syntax, and git whitespace checks passed.

## Commits
- `048fb36`: archive fields and transactional dependency resolution.
- `e90eae8`: per-product review UI, confirmation, validation, and visibility rules.

## Deviations
- Fixed owner product visibility to include inactive products so held products can be found and reactivated; archived products remain excluded.
- Added availability checks to new-order services so stale/API product references cannot bypass hold/archive status.
