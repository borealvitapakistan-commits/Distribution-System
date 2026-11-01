# Unified product save

Implemented in `7fc4706`.

- Both top and bottom buttons say Save and submit one form, including manufacturer pricing. The bottom button is after all pricing cards. Removed individual manufacturer save buttons.
- Manufacturer fields use distinct names per manufacturer. The existing transaction saves product details, ingredients, media, allocations and every submitted price list together. Unticked manufacturers cannot be reallocated by a stale pricing card.
- Invalid product or pricing data stays in the editor with entered price rows preserved; invalid prices roll back all database edits. Adding/removing price rows marks the editor dirty, and removed manufacturers' pricing inputs are disabled.
- Success remains on the product editor with one bottom confirmation: "Product has been saved." Existing back link and browser Back remain available.
- Validation: all 75 product tests pass, JavaScript syntax/migration drift/whitespace checks pass. Isolated browser checks exercised both Save buttons with simultaneous title/cost/retail edits, bottom confirmation positioning, no nested forms, browser Back and a 390px mobile viewport without horizontal overflow.

No schema change was needed. This supersedes the separate save behavior noted in 01-06.
