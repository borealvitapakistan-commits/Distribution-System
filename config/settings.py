from pathlib import Path

import environ


BASE_DIR = Path(__file__).resolve().parent.parent


# Environment variables
env = environ.Env(
    DEBUG=(bool, False),
)

environ.Env.read_env(BASE_DIR / ".env")


SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")

ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=[
        "127.0.0.1",
        "localhost",
    ],
)


# Applications
INSTALLED_APPS = [
    # Django applications
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Django REST Framework
    "rest_framework",
    "rest_framework.authtoken",

    # Project applications
    "apps.accounts.apps.AccountsConfig",
    "apps.core.apps.CoreConfig",
    "apps.audit.apps.AuditConfig",

    # Business applications
    "apps.owners.apps.OwnersConfig",
    "apps.distributors.apps.DistributorsConfig",
    "apps.agreements.apps.AgreementsConfig",
    "apps.batches.apps.BatchesConfig",
    "apps.manufacturers.apps.ManufacturersConfig",
    "apps.products.apps.ProductsConfig",
    "apps.owner_warehouse.apps.OwnerWarehouseConfig",
    "apps.owner_inventory.apps.OwnerInventoryConfig",
    "apps.requests.apps.RequestsConfig",
    "apps.finance.apps.FinanceConfig",

    # Distributor-only inventory/warehouse (fully separate from the
    # Owner's — different tables, no FK relationship between them).
    "apps.distributor_warehouse.apps.DistributorWarehouseConfig",
    "apps.distributor_inventory.apps.DistributorInventoryConfig",
]


# Middleware
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


ROOT_URLCONF = "config.urls"


# Templates
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            BASE_DIR / "templates",
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.brand",
                "apps.core.navigation.nav_section",
                "apps.core.context_processors.owner_notifications",
                "apps.core.context_processors.distributor_notifications",
            ],
        },
    },
]


WSGI_APPLICATION = "config.wsgi.application"


# SQLite database
# The one database: Supabase PostgreSQL, from DATABASE_URL in .env (the
# Session pooler connection string). There is no local fallback — without
# it the app refuses to start rather than quietly using another database.
if not env("DATABASE_URL", default=""):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "DATABASE_URL is missing from .env. Add the Supabase Session pooler "
        "connection string (Supabase > Connect > Session pooler)."
    )
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
# A pool of open connections shared by every request: opening a fresh
# connection to Supabase costs over a second, so each page borrows one
# that is already open instead. (A pool needs CONN_MAX_AGE = 0.)
DATABASES["default"]["CONN_MAX_AGE"] = 0
DATABASES["default"].setdefault("OPTIONS", {}).update({
    "sslmode": "require",
    "pool": {
        "min_size": env.int("DB_POOL_MIN", default=2),
        "max_size": env.int("DB_POOL_MAX", default=10),
        "timeout": 30,
        # Supabase's pooler drops idle connections; recycle well before.
        "max_idle": 240,
        "max_lifetime": 1800,
    },
})


# The automated tests never touch Supabase: they build and throw away a
# temporary in-memory database on each run (the app itself only ever
# uses DATABASE_URL above).
import sys

if len(sys.argv) > 1 and sys.argv[1] == "test":
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}


# An in-memory cache: things read on every page (the login session, the
# brand) come from here instead of a round trip to the database.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "distribution-system",
    }
}
# Sessions are read from the cache and still saved to the database, so a
# server restart doesn't sign anyone out.
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "MinimumLengthValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "CommonPasswordValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "NumericPasswordValidator"
        ),
    },
]


# Language and timezone
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Karachi"

USE_I18N = True
USE_TZ = True


# Static files
STATIC_URL = "/static/"

STATICFILES_DIRS = [
    BASE_DIR / "static",
]

STATIC_ROOT = BASE_DIR / "staticfiles"


# Media files
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Payment proofs (PDFs) are previewed in an in-page frame, so allow
# same-origin framing while still refusing other sites.
X_FRAME_OPTIONS = "SAMEORIGIN"


# Default primary key
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Custom user model
AUTH_USER_MODEL = "accounts.User"


# Authentication redirects
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"


# Shopify: product import, plus inventory sync once SHOPIFY_INVENTORY_SYNC
# is on. This system holds the stock; Shopify is told the sellable total
# and its orders are imported as sales.
# Slack incoming webhook for "Share to Slack" on orders (blank = off).
SLACK_WEBHOOK_URL = env("SLACK_WEBHOOK_URL", default="")

SHOPIFY_STORE_DOMAIN = env("SHOPIFY_STORE_DOMAIN", default="")
SHOPIFY_CLIENT_ID = env("SHOPIFY_CLIENT_ID", default="")
SHOPIFY_CLIENT_SECRET = env("SHOPIFY_CLIENT_SECRET", default="")
SHOPIFY_API_VERSION = env("SHOPIFY_API_VERSION", default="2026-07")
SHOPIFY_INVENTORY_SYNC = env.bool("SHOPIFY_INVENTORY_SYNC", default=False)
SHOPIFY_LOCATION_ID = env("SHOPIFY_LOCATION_ID", default="")


# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}