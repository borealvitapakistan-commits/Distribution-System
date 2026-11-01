# Product editor simplification

Implemented the user's requested product form in `e3ed415`.

- Types: Capsule, Tablet, Liquid (syrup), Essential Oil, Carrier Oil, Powder, Herbal Tea. Units are derived on both the server and browser: count, mL, grams, count respectively. Old categories remain in the database for historical records.
- Removed general package size, base price, shipping, supplemental/additional metadata and SKU fields from the editor. Existing values remain saved. New products get an internal unique identifier without SKU entry; barcode is optional.
- Label claim is optional and collapsed. Its nested ingredient list exposes ingredient, quantity (free text such as 500 mg), medicinal status and deletion. Removed ingredient metadata remains saved on edits.
- Fixed ingredient edit validation: validate replacement rows before removing old rows, without colliding with their uniqueness constraint.
- Manufacturer pricing now shows size in the appropriate unit, manufacturer price and fixed retail price. Existing backend linked-price support remains compatible; no existing local price rows were linked. Product details and each manufacturer's prices retain their separate Save buttons.

Validation: 390 Django tests passed; JavaScript syntax, migration drift and whitespace checks passed. Desktop/mobile browser checks used an isolated database on port 8001: seven type mappings, creation without SKU/barcode, adding and saving ingredients, manufacturer cost changes leaving retail unchanged, and no horizontal mobile overflow.

Migration `products.0015_optional_product_sku` applied locally. Database backup: `data/backups/product-editor-20261101.sqlite3`. Browser screenshots: `/tmp/product-editor-desktop.png`, `/tmp/product-pricing-mobile.png`.
