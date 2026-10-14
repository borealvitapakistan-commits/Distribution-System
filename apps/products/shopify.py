"""Shopify Admin API client.

Credentials come from a Dev Dashboard app installed on the store
(client ID + secret in .env). Each call swaps them for a short-lived
access token via the client-credentials grant, cached until it expires.

The app reads products and orders, and writes one thing only: inventory
quantities, because this system is the inventory Shopify sells from.
Any other write scope on the app is refused.
"""

import json
import uuid
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.cache import cache


class ShopifyError(Exception):
    pass


TOKEN_CACHE_KEY = "shopify-access-token"

PRODUCTS_QUERY = """
query Products($cursor: String) {
  products(first: 50, after: $cursor, sortKey: TITLE) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      title
      handle
      descriptionHtml
      vendor
      productType
      tags
      status
      featuredMedia { preview { image { url } } }
      variants(first: 50) {
        nodes { sku barcode price compareAtPrice title }
      }
    }
  }
}
"""


def is_configured():
    return all(
        [
            settings.SHOPIFY_STORE_DOMAIN,
            settings.SHOPIFY_CLIENT_ID,
            settings.SHOPIFY_CLIENT_SECRET,
        ]
    )


def _request(url, *, body, headers):
    request = urllib.request.Request(url, data=body, headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise ShopifyError(f"Shopify answered {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ShopifyError(f"Could not reach Shopify: {exc.reason}") from exc


def _access_token():
    token = cache.get(TOKEN_CACHE_KEY)
    if token:
        return token

    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": settings.SHOPIFY_CLIENT_ID,
            "client_secret": settings.SHOPIFY_CLIENT_SECRET,
        }
    ).encode()
    data = _request(
        f"https://{settings.SHOPIFY_STORE_DOMAIN}/admin/oauth/access_token",
        body=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )

    token = data.get("access_token")
    if not token:
        raise ShopifyError("Shopify did not return an access token.")

    lifetime = int(data.get("expires_in") or 3600)
    cache.set(TOKEN_CACHE_KEY, token, max(lifetime - 300, 60))
    return token


def graphql(query, variables=None):
    data = _request(
        f"https://{settings.SHOPIFY_STORE_DOMAIN}/admin/api/"
        f"{settings.SHOPIFY_API_VERSION}/graphql.json",
        body=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": _access_token(),
        },
    )

    if data.get("errors"):
        message = "; ".join(error.get("message", "") for error in data["errors"])
        raise ShopifyError(message)

    return data["data"]


ALLOWED_WRITE_SCOPES = {"write_inventory", "write_products", "write_publications"}

INVENTORY_SYNC_SCOPES = (
    "read_products",
    "read_orders",
    "read_inventory",
    "read_locations",
    "write_inventory",
)

# Needed only to create products on Shopify that exist here but not there,
# and to put them on the Online Store. Without them, sync still keeps the
# quantities of products already on Shopify in step.
PRODUCT_PUBLISH_SCOPES = (
    "write_products",
    "read_publications",
    "write_publications",
)


def require_scopes(*needed):
    """Refuse to go on unless the app has every scope in needed and no
    write access beyond inventory, products and publishing. Returns every
    scope the app has."""
    data = graphql("{ currentAppInstallation { accessScopes { handle } } }")
    scopes = {
        scope["handle"]
        for scope in data["currentAppInstallation"]["accessScopes"]
    }

    writes = sorted(
        scope for scope in scopes
        if scope.startswith("write_") and scope not in ALLOWED_WRITE_SCOPES
    )
    if writes:
        raise ShopifyError(
            "The Shopify app has write access (" + ", ".join(writes) + "). "
            "Remove every write_ scope except " + ", ".join(sorted(ALLOWED_WRITE_SCOPES))
            + ", then try again."
        )

    missing = [scope for scope in needed if scope not in scopes]
    if missing:
        raise ShopifyError(
            "The Shopify app is missing: " + ", ".join(missing) + ". Add "
            "the scope(s), release the version and approve it on the store."
        )

    return scopes


def fetch_products():
    require_scopes("read_products")

    products = []
    cursor = None

    while True:
        page = graphql(PRODUCTS_QUERY, {"cursor": cursor})["products"]

        for node in page["nodes"]:
            media = node.get("featuredMedia") or {}
            image = ((media.get("preview") or {}).get("image") or {}).get("url", "")

            products.append(
                {
                    "id": node["id"].rsplit("/", 1)[-1],
                    "title": node["title"].strip(),
                    "handle": node["handle"],
                    "description_html": node.get("descriptionHtml") or "",
                    "vendor": node.get("vendor") or "",
                    "product_type": node.get("productType") or "",
                    "tags": node.get("tags") or [],
                    "active": node.get("status") == "ACTIVE",
                    "image_url": image,
                    "variants": [
                        {
                            "sku": (variant.get("sku") or "").strip(),
                            "barcode": (variant.get("barcode") or "").strip(),
                            "price": variant.get("price") or "0",
                            "compare_at_price": variant.get("compareAtPrice"),
                            "title": variant.get("title") or "",
                        }
                        for variant in node["variants"]["nodes"]
                    ],
                }
            )

        if not page["pageInfo"]["hasNextPage"]:
            return products

        cursor = page["pageInfo"]["endCursor"]


def download_image(url):
    """(filename, bytes) for a product image on Shopify's CDN."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ShopifyError("Image URL must be https.")

    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            content = response.read(15 * 1024 * 1024 + 1)
    except urllib.error.URLError as exc:
        raise ShopifyError(f"Could not download image: {exc}") from exc

    if len(content) > 15 * 1024 * 1024:
        raise ShopifyError("Image is larger than 15 MB.")

    return parsed.path.rsplit("/", 1)[-1] or "image.jpg", content


LOCATIONS_QUERY = """
query Locations {
  locations(first: 20) { nodes { id name isActive fulfillsOnlineOrders } }
}
"""

PRODUCT_VARIANTS_QUERY = """
query ProductVariants($id: ID!) {
  product(id: $id) {
    variants(first: 10) {
      nodes { id inventoryItem { id tracked } }
    }
  }
}
"""

INVENTORY_LEVELS_QUERY = """
query InventoryLevels($ids: [ID!]!, $locationId: ID!) {
  nodes(ids: $ids) {
    ... on InventoryItem {
      id
      tracked
      inventoryLevel(locationId: $locationId) {
        quantities(names: ["available"]) { name quantity }
      }
    }
  }
}
"""

ORDERS_QUERY = """
query Orders($cursor: String, $query: String) {
  orders(first: 50, after: $cursor, query: $query, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      createdAt
      cancelledAt
      displayFinancialStatus
      lineItems(first: 100) {
        nodes {
          id title quantity product { id }
          originalUnitPriceSet { shopMoney { amount } }
          discountedUnitPriceAfterAllDiscountsSet { shopMoney { amount } }
        }
      }
    }
  }
}
"""

TRACK_INVENTORY_MUTATION = """
mutation TrackInventory($id: ID!) {
  inventoryItemUpdate(id: $id, input: { tracked: true }) {
    userErrors { field message }
  }
}
"""

SET_QUANTITIES_MUTATION = """
mutation SetQuantities($input: InventorySetQuantitiesInput!, $idempotencyKey: String!) {
  inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    userErrors { code field message }
  }
}
"""


def _gid(kind, value):
    value = str(value)
    return value if value.startswith("gid://") else f"gid://shopify/{kind}/{value}"


def _numeric_id(gid):
    return gid.rsplit("/", 1)[-1] if gid else ""


def _money_amount(money_set):
    """The shop-currency amount of a Shopify MoneyBag, as a string, or
    None when Shopify didn't send one."""
    return ((money_set or {}).get("shopMoney") or {}).get("amount")


def _raise_user_errors(errors):
    if errors:
        raise ShopifyError("; ".join(error.get("message", "") for error in errors))


def pick_location(location_id=""):
    """(id, name) of the Shopify location stock is kept at — the one
    named in SHOPIFY_LOCATION_ID, else the first active location that
    fulfils online orders."""
    locations = graphql(LOCATIONS_QUERY)["locations"]["nodes"]

    if location_id:
        wanted = _gid("Location", location_id)
        for location in locations:
            if location["id"] == wanted:
                return location["id"], location["name"]
        raise ShopifyError(f"Shopify has no location {location_id}.")

    for location in locations:
        if location["isActive"] and location["fulfillsOnlineOrders"]:
            return location["id"], location["name"]

    raise ShopifyError("Shopify has no active location that fulfils online orders.")


def inventory_item_for_product(shopify_product_id):
    """(inventory item gid, tracked, variant count) for a Shopify
    product's first variant. One product here is one stock count in
    Shopify, so products are expected to have a single variant."""
    product = graphql(
        PRODUCT_VARIANTS_QUERY, {"id": _gid("Product", shopify_product_id)}
    )["product"]

    if product is None:
        raise ShopifyError(f"Shopify product {shopify_product_id} no longer exists.")

    variants = product["variants"]["nodes"]
    if not variants:
        raise ShopifyError(f"Shopify product {shopify_product_id} has no variants.")

    item = variants[0]["inventoryItem"]
    return item["id"], item["tracked"], len(variants)


def track_inventory(inventory_item_id):
    data = graphql(TRACK_INVENTORY_MUTATION, {"id": inventory_item_id})
    _raise_user_errors(data["inventoryItemUpdate"]["userErrors"])


def fetch_available(inventory_item_ids, location_id):
    """{inventory item gid: available quantity, or None if the item
    isn't stocked at that location}."""
    if not inventory_item_ids:
        return {}

    nodes = graphql(
        INVENTORY_LEVELS_QUERY,
        {"ids": list(inventory_item_ids), "locationId": location_id},
    )["nodes"]

    available = {}
    for node in nodes:
        if not node:
            continue
        level = node.get("inventoryLevel") or {}
        quantities = {q["name"]: q["quantity"] for q in level.get("quantities") or []}
        available[node["id"]] = quantities.get("available")

    return available


def set_available(quantities, location_id, reference=""):
    """quantities: {inventory item gid: int}. Overwrites Shopify's
    available count with no compare-and-swap — this system is the
    source of truth."""
    items = list(quantities.items())

    for start in range(0, len(items), 250):
        data = graphql(
            SET_QUANTITIES_MUTATION,
            {
                "input": {
                    "name": "available",
                    "reason": "correction",
                    "referenceDocumentUri": reference or None,
                    "quantities": [
                        {
                            "inventoryItemId": item_id,
                            "locationId": location_id,
                            "quantity": quantity,
                            "changeFromQuantity": None,
                        }
                        for item_id, quantity in items[start:start + 250]
                    ],
                },
                "idempotencyKey": str(uuid.uuid4()),
            },
        )
        _raise_user_errors(data["inventorySetQuantities"]["userErrors"])


def fetch_orders(created_after):
    """Every order created after created_after (an aware datetime),
    oldest first."""
    query = "created_at:>'" + created_after.isoformat() + "'"
    orders = []
    cursor = None

    while True:
        page = graphql(ORDERS_QUERY, {"cursor": cursor, "query": query})["orders"]

        for node in page["nodes"]:
            orders.append(
                {
                    "id": _numeric_id(node["id"]),
                    "name": node["name"],
                    "created_at": node["createdAt"],
                    "cancelled": bool(node.get("cancelledAt")),
                    "financial_status": node.get("displayFinancialStatus") or "",
                    "lines": [
                        {
                            "id": _numeric_id(line["id"]),
                            "title": line["title"],
                            "quantity": line["quantity"],
                            "product_id": _numeric_id(
                                (line.get("product") or {}).get("id")
                            ),
                            "original_unit_price": _money_amount(
                                line.get("originalUnitPriceSet")
                            ),
                            "unit_price": _money_amount(
                                line.get("discountedUnitPriceAfterAllDiscountsSet")
                            ),
                        }
                        for line in node["lineItems"]["nodes"]
                    ],
                }
            )

        if not page["pageInfo"]["hasNextPage"]:
            return orders

        cursor = page["pageInfo"]["endCursor"]


PRODUCT_LIST_QUERY = """
query ShopifyProducts($cursor: String) {
  products(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes { id title handle variants(first: 5) { nodes { sku } } }
  }
}
"""

CREATE_PRODUCT_MUTATION = """
mutation CreateProduct($input: ProductSetInput!) {
  productSet(synchronous: true, input: $input) {
    product {
      id
      variants(first: 1) { nodes { id inventoryItem { id } } }
    }
    userErrors { field message code }
  }
}
"""

STAGED_UPLOAD_MUTATION = """
mutation StagedUpload($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) {
    stagedTargets { url resourceUrl parameters { name value } }
    userErrors { field message }
  }
}
"""

PUBLICATIONS_QUERY = """
query Publications {
  publications(first: 20) { nodes { id catalog { title } } }
}
"""

PUBLISH_MUTATION = """
mutation Publish($id: ID!, $input: [PublicationInput!]!) {
  publishablePublish(id: $id, input: $input) {
    userErrors { field message }
  }
}
"""


def list_products():
    """Every Shopify product, light: [{id, title, handle, skus}]."""
    products = []
    cursor = None

    while True:
        page = graphql(PRODUCT_LIST_QUERY, {"cursor": cursor})["products"]

        for node in page["nodes"]:
            products.append(
                {
                    "id": _numeric_id(node["id"]),
                    "title": node["title"],
                    "handle": node["handle"],
                    "skus": [
                        (variant.get("sku") or "").strip().upper()
                        for variant in node["variants"]["nodes"]
                        if variant.get("sku")
                    ],
                }
            )

        if not page["pageInfo"]["hasNextPage"]:
            return products

        cursor = page["pageInfo"]["endCursor"]


def upload_image(filename, content, mime_type):
    """Uploads an image file to Shopify's storage (Shopify can't fetch it
    from this server, which isn't on the internet) and returns the URL a
    product can use as its image source."""
    data = graphql(
        STAGED_UPLOAD_MUTATION,
        {
            "input": [
                {
                    "resource": "IMAGE",
                    "filename": filename,
                    "mimeType": mime_type,
                    "httpMethod": "POST",
                }
            ]
        },
    )["stagedUploadsCreate"]
    _raise_user_errors(data["userErrors"])
    target = data["stagedTargets"][0]

    boundary = uuid.uuid4().hex
    parts = []
    for parameter in target["parameters"]:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{parameter["name"]}"'
            f'\r\n\r\n{parameter["value"]}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"'
        f"\r\nContent-Type: {mime_type}\r\n\r\n".encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )

    request = urllib.request.Request(
        target["url"],
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60):
            pass
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise ShopifyError(f"Image upload failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise ShopifyError(f"Image upload failed: {exc.reason}") from exc

    return target["resourceUrl"]


def create_product(product_input):
    """Creates one product (single variant) from a ProductSetInput.
    Returns (numeric product id, product gid, inventory item gid)."""
    data = graphql(CREATE_PRODUCT_MUTATION, {"input": product_input})["productSet"]
    _raise_user_errors(data["userErrors"])

    product = data["product"]
    item_id = product["variants"]["nodes"][0]["inventoryItem"]["id"]
    return _numeric_id(product["id"]), product["id"], item_id


def online_store_publication_id():
    for publication in graphql(PUBLICATIONS_QUERY)["publications"]["nodes"]:
        if ((publication.get("catalog") or {}).get("title") or "").lower() == "online store":
            return publication["id"]
    return None


def publish(product_gid, publication_id):
    data = graphql(
        PUBLISH_MUTATION,
        {"id": product_gid, "input": [{"publicationId": publication_id}]},
    )
    _raise_user_errors(data["publishablePublish"]["userErrors"])
