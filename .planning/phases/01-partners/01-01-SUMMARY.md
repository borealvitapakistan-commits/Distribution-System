# Partners navigation and manufacturer deletion — complete

## Changes
- Added an owner-only Partners sidebar menu with Manufacturers, Owners, Brand, and Distributors; moved those links out of Settings.
- Added animated expansion/collapse, collapse before navigation, active section styling, Enter/Escape controls, and reduced-motion support.
- Added Delete buttons on manufacturer list and detail pages using the existing confirmation-form pattern.
- Added an owner-only POST endpoint and transactional deletion service. Successful deletions are audited; protected product, package-price, and order references produce a useful message without losing data or recording a successful deletion.

## Validation
- 71 Django tests passed across core, manufacturers, owners, and distributors.
- Deletion tests cover successful removal/audit, linked products/pricing/orders, missing records, GET, anonymous/distributor access, and CSRF enforcement.
- Playwright verified all four menu destinations, collapse after navigation, highlighting, keyboard controls, mobile layout, reduced motion, and cancellation of the delete confirmation. All three existing manufacturers remained present.
- Django system checks, JavaScript syntax, and git diff whitespace checks passed.

## Commits
- `1b9bb47`: animated Partners sidebar menu.
- `de6359e`: confirmed deletion of unused manufacturers.

## Decisions
- Preserved existing PROTECT relationships. Linked manufacturers can be retired using the existing Active field.
- No schema migration required.

## Deviations
None.
