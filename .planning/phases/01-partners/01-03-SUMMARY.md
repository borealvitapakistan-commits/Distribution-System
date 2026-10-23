# Remove manufacturer payment-before-shipping terms — complete

## Changes
- Removed the preset payment-before-shipping percentage from manufacturer create/edit forms and Django admin.
- Removed the manufacturer's usual advance display, percentage shortcut, and calculated default amount from the order advance screen.
- Advance payment amounts now start blank and can be entered per order; full-payment entry remains available.
- Existing stored terms and payment records were preserved; no schema migration required.

## Validation
- 24 existing manufacturer rules, order, and payment-flow tests passed.
- Rendered create, edit, admin, and order advance screens checked with an unsaved verification manufacturer carrying an old 30% term: no preset term shown and advance amount blank.
- No database records changed during screen verification.
- Migration drift and git whitespace checks passed.

## Deviations
None.
