"""Serves an Owner's repeat visit to a page from memory when nothing has
changed since it was built.

Correct across several servers (Vercel runs more than one): a single
"data version" number lives in the database, every request that can
change data (POST, PUT, PATCH, DELETE — every form in the app) raises it,
and a saved page is only reused while the number is the same. Checking
the number is one tiny query instead of the page's usual dozen.
"""
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse
from django.utils import timezone

PAGE_TTL = 10 * 60
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Pages that show things which change outside this app (Shopify's own
# numbers) or are downloads rather than pages.
SKIP_PREFIXES = ("/owner/inventory/shopify/", "/admin/", "/accounts/", "/api/", "/health/", "/owner/settings/data/")
SKIP_PARAMS = ("format", "download", "part")


def current_version():
    with connection.cursor() as cursor:
        cursor.execute("SELECT number FROM core_dataversion WHERE id = 1")
        row = cursor.fetchone()
    return row[0] if row else 0


def bump_version():
    """Call after changing data outside a request (e.g. a management
    command), so no saved page is shown with the old data."""
    with connection.cursor() as cursor:
        cursor.execute("UPDATE core_dataversion SET number = number + 1 WHERE id = 1")


class PageCacheMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in UNSAFE_METHODS:
            response = self.get_response(request)
            bump_version()
            return response

        key = self._key(request)
        if key is None:
            return self.get_response(request)

        saved = cache.get(key)
        if saved is not None:
            response = HttpResponse(saved["content"], content_type=saved["content_type"])
            response["X-Page-Cache"] = "hit"
            return response

        response = self.get_response(request)
        if (
            response.status_code == 200
            and not response.streaming
            and response.get("Content-Type", "").startswith("text/html")
            and not response.cookies
        ):
            cache.set(
                key,
                {"content": response.content, "content_type": response["Content-Type"]},
                PAGE_TTL,
            )
        return response

    @staticmethod
    def _key(request):
        if request.method != "GET":
            return None
        path = request.path
        if path.startswith(SKIP_PREFIXES) or any(name in request.GET for name in SKIP_PARAMS):
            return None
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated and getattr(user, "is_owner", False)):
            return None
        # A page showing a one-time notice ("Saved") must be built fresh.
        if len(get_messages(request)):
            return None
        session = request.session.session_key or ""
        return (
            f"page:{current_version()}:{user.pk}:{session}:"
            f"{timezone.localdate():%Y%m%d}:{request.get_full_path()}"
        )
