"""
Ledgerline ERP settings.

All secrets and environment-specific values come from environment variables.
Production refuses to start with insecure defaults (see the checks at the bottom).
"""
import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return env(name, str(default)).lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [v.strip() for v in env(name, default).split(",") if v.strip()]


ERP_ENV = env("ERP_ENV", "dev")  # dev | test | prod
DEBUG = env_bool("ERP_DEBUG", ERP_ENV == "dev")
SECRET_KEY = env("ERP_SECRET_KEY", "dev-insecure-key-change-me-0123456789-abcdefghijklmnop" if ERP_ENV != "prod" else None)
# Separate key used to encrypt secrets at rest (MFA seeds, webhook secrets).
FIELD_ENCRYPTION_KEY = env("ERP_FIELD_ENCRYPTION_KEY", "dev-field-key-change-me" if ERP_ENV != "prod" else None)
ALLOWED_HOSTS = env_list("ERP_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    "apps.core",
    "apps.iam",
    "apps.ledger",
    "apps.partners",
    "apps.inventory",
    "apps.billing",
    "apps.sales",
    "apps.purchasing",
    "apps.approvals",
    "apps.integrations",
    "apps.assets",
    "apps.einvoice",
    "apps.banking",
    "apps.planning",
    "apps.manufacturing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.RequestContextMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "erp.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
WSGI_APPLICATION = "erp.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("ERP_DB_NAME", "erp"),
        "USER": env("ERP_DB_USER", "erp"),
        "PASSWORD": env("ERP_DB_PASSWORD", "erp_dev_pw"),
        "HOST": env("ERP_DB_HOST", "localhost"),
        "PORT": env("ERP_DB_PORT", "5432"),
        "ATOMIC_REQUESTS": True,  # every API request is one DB transaction
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"sslmode": env("ERP_DB_SSLMODE", "prefer")},
    }
}

AUTH_USER_MODEL = "iam.User"
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "apps.iam.validators.ComplexityValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("ERP_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
MEDIA_ROOT = Path(env("ERP_MEDIA_ROOT", str(BASE_DIR / "media")))
ERP_MAX_UPLOAD_MB = int(env("ERP_MAX_UPLOAD_MB", "10"))
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- REST API -------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.integrations.auth.ApiKeyAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.exceptions.exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": env("ERP_THROTTLE_USER", "2000/hour"),
        "anon": env("ERP_THROTTLE_ANON", "60/hour"),
        "login": env("ERP_THROTTLE_LOGIN", "10/minute"),
        "refresh": env("ERP_THROTTLE_REFRESH", "600/hour"),
    },
    "COERCE_DECIMAL_TO_STRING": True,  # never send money as float
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(env("ERP_ACCESS_TOKEN_MINUTES", "15"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(hours=int(env("ERP_REFRESH_TOKEN_HOURS", "10"))),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Ledgerline ERP API",
    "DESCRIPTION": "Accounting, sales, purchasing and inventory API. "
    "Send `X-Company: <id>` on every request. Authenticate with a JWT "
    "(`Authorization: Bearer ...`) or an integration key (`Authorization: Api-Key ...`).",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

# --- Account security -----------------------------------------------------
ERP_MAX_FAILED_LOGINS = int(env("ERP_MAX_FAILED_LOGINS", "5"))
ERP_LOCKOUT_MINUTES = int(env("ERP_LOCKOUT_MINUTES", "30"))
# Roles at or above this level must have MFA enabled to log in.
ERP_MFA_REQUIRED_LEVEL = int(env("ERP_MFA_REQUIRED_LEVEL", "4"))
ERP_PASSWORD_MAX_AGE_DAYS = int(env("ERP_PASSWORD_MAX_AGE_DAYS", "90"))

# --- CORS / CSRF / transport ---------------------------------------------
CORS_ALLOWED_ORIGINS = env_list("ERP_CORS_ORIGINS", "http://localhost:5173")
CORS_ALLOW_HEADERS = ["authorization", "content-type", "x-company", "x-request-id", "idempotency-key"]
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

if ERP_ENV == "prod":
    SECURE_SSL_REDIRECT = env_bool("ERP_SSL_REDIRECT", True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    for _name, _val in (("ERP_SECRET_KEY", SECRET_KEY), ("ERP_FIELD_ENCRYPTION_KEY", FIELD_ENCRYPTION_KEY)):
        if not _val or len(_val) < 50:
            raise RuntimeError(f"{_name} must be set to a random value of at least 50 characters in production")
    if DEBUG:
        raise RuntimeError("ERP_DEBUG must be off in production")
    if DATABASES["default"]["PASSWORD"] == "erp_dev_pw":
        raise RuntimeError("ERP_DB_PASSWORD must be set in production")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("ERP_LOG_LEVEL", "INFO")},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}
