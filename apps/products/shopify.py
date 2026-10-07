"""Read-only Shopify Admin API client.

Credentials come from a Dev Dashboard app installed on the store
(client ID + secret in .env). Each call swaps them for a short-lived
access token via the client-credentials grant, cached until it expires.
"""

import json
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


def require_read_only_access():
    """Refuse to go on unless the key can read products and can't write
    anything — the app must only ever look at the store."""
    data = graphql("{ currentAppInstallation { accessScopes { handle } } }")
    scopes = {
        scope["handle"]
        for scope in data["currentAppInstallation"]["accessScopes"]
    }

    writes = sorted(scope for scope in scopes if scope.startswith("write_"))
    if writes:
        raise ShopifyError(
            "The Shopify app has write access (" + ", ".join(writes) + "). "
            "Remove every write_ scope so it is read-only, then try again."
        )

    if "read_products" not in scopes:
        raise ShopifyError(
            "The Shopify app can't read products yet. Add the read_products "
            "scope, release the version and approve it on the store."
        )


def fetch_products():
    require_read_only_access()

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
