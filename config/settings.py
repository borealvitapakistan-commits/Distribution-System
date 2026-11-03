import sys
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent


# Environment variables
env = environ.Env(
    DEBUG=(bool, False),
)

# Vercel supplies secrets through its environment, never through a bundled .env.
IS_VERCEL = env.bool("VERCEL", default=False)
TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"
if not IS_VERCEL:
    environ.Env.read_env(BASE_DIR / ".env")


SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG") and not IS_VERCEL

ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=[
        "127.0.0.1",
        "localhost",
    ],
)
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
# Trust only this project's URLs, rather than every site on vercel.app.
if IS_VERCEL:
    for variable in ("VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_BRANCH_URL"):
        hostname = env(variable, default="").strip()
        if hostname:
            ALLOWED_HOSTS.append(hostname)
            CSRF_TRUSTED_ORIGINS.append(f"https://{hostname}")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if IS_VERCEL else None
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=not DEBUG and not TESTING)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000 if not DEBUG else 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG


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
    "apps.batches.apps.BatchesConfig",
    "apps.manufacturers.apps.ManufacturersConfig",
    "apps.products.apps.ProductsConfig",
    "apps.owner_warehouse.apps.OwnerWarehouseConfig",
    "apps.owner_inventory.apps.OwnerInventoryConfig",
    "apps.finance.apps.FinanceConfig",
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
            ],
        },
    },
]


WSGI_APPLICATION = "config.wsgi.application"


# Supabase PostgreSQL. The session pooler works locally; use the transaction
# pooler on Vercel and the session pooler for migrations in GitHub Actions.
if not env("DATABASE_URL", default=""):
    raise ImproperlyConfigured(
        "DATABASE_URL is missing. Copy a PostgreSQL connection string from "
        "Supabase > Connect into your environment."
    )
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
# A pool of open connections shared by every request: opening a fresh
# connection to Supabase costs over a second, so each page borrows one
# that is already open instead. (A pool needs CONN_MAX_AGE = 0.)
DATABASES["default"]["CONN_MAX_AGE"] = 0
# Check each connection on checkout so sockets closed by the remote pooler
# are replaced before a page's first query uses them.
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
DATABASES["default"].setdefault("OPTIONS", {}).update({
    "sslmode": "require",
    "pool": {
        "min_size": env.int("DB_POOL_MIN", default=0 if IS_VERCEL else 2),
        "max_size": env.int("DB_POOL_MAX", default=1 if IS_VERCEL else 10),
        "timeout": 30,
        # Limit spare idle connections and periodically refresh the pool.
        "max_idle": 240,
        "max_lifetime": 1800,
    },
    "connect_timeout": 10,
    # Supabase's transaction pooler does not support prepared statements.
    "prepare_threshold": None,
})
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = env.bool(
    "DB_TRANSACTION_POOLER", default=IS_VERCEL,
)


# The automated tests never touch Supabase: they build and throw away a
# temporary in-memory database on each run (the app itself only ever
# uses DATABASE_URL above).
if TESTING:
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

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
USE_S3_STORAGE = env.bool("USE_S3_STORAGE", default=IS_VERCEL)
if IS_VERCEL and not USE_S3_STORAGE:
    raise ImproperlyConfigured("Vercel requires USE_S3_STORAGE=True for persistent uploads.")
if USE_S3_STORAGE:
    from botocore.config import Config

    storage_variables = (
        "S3_ENDPOINT_URL", "S3_REGION_NAME", "S3_BUCKET_NAME",
        "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY",
    )
    missing = [name for name in storage_variables if not env(name, default="")]
    if missing:
        raise ImproperlyConfigured("Missing private upload storage settings: " + ", ".join(missing))
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "endpoint_url": env("S3_ENDPOINT_URL"),
            "region_name": env("S3_REGION_NAME"),
            "bucket_name": env("S3_BUCKET_NAME"),
            "access_key": env("S3_ACCESS_KEY_ID"),
            "secret_key": env("S3_SECRET_ACCESS_KEY"),
            "default_acl": None,
            "file_overwrite": False,
            "querystring_auth": True,
            "querystring_expire": 3600,
            "client_config": Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=10,
                read_timeout=30,
                retries={"max_attempts": 2},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        },
    }

# Temporary upload/PDF buffers use the operating system's temporary directory.
# Permanent uploads always go through Django's default storage above.
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend" if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="webmaster@localhost")

# Local payment proofs are served by Django and previewed in a frame.
# Production proofs use signed storage URLs; application pages need no framing.
X_FRAME_OPTIONS = "SAMEORIGIN" if DEBUG else "DENY"


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
