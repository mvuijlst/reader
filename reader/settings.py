"""Django settings for reader — a single-user, Google Reader-style feed reader.

Configuration comes from .env (python-dotenv); see .env.example for the keys.
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "t", "yes", "y", "on")


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-only-key",  # overridden in production via .env
)

DEBUG = env_bool("DJANGO_DEBUG", default=False)

if not DEBUG and SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY in .env (see .env.example) before running with DEBUG off.")

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if "." in h]

# --- reader-specific --------------------------------------------------------

# How often a healthy feed is polled. The systemd timer runs `fetch_feeds`
# more often than this; each run only fetches feeds that are due.
READER_FETCH_INTERVAL_MINUTES = env_int("READER_FETCH_INTERVAL_MINUTES", 30)

# Parallel HTTP fetches per run.
READER_FETCH_WORKERS = env_int("READER_FETCH_WORKERS", 8)

# Like Google Reader: unread items older than this are marked read
# automatically, so a neglected feed doesn't sit at "1000+" forever. 0 = never.
READER_AUTO_READ_DAYS = env_int("READER_AUTO_READ_DAYS", 30)

# Read, unstarred items older than this are deleted once they have also
# dropped out of their feed. Starred items are kept forever. 0 = keep all.
READER_KEEP_DAYS = env_int("READER_KEEP_DAYS", 180)

READER_USER_AGENT = os.environ.get(
    "READER_USER_AGENT",
    "Mozilla/5.0 (compatible; Reader/1.0; personal feed reader)",
)

READER_PAGE_SIZE = 30

# ----------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.feeds",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.feeds.middleware.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "reader.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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

WSGI_APPLICATION = "reader.wsgi.application"

# WAL lets the fetch timer write while the web app reads; IMMEDIATE avoids
# "database is locked" upgrades mid-transaction.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            "init_command": (
                "PRAGMA journal_mode=WAL;"
                "PRAGMA synchronous=NORMAL;"
                "PRAGMA busy_timeout=10000;"
            ),
            "transaction_mode": "IMMEDIATE",
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "login"

# One user, a few devices: stay logged in for a year.
SESSION_COOKIE_AGE = 365 * 24 * 3600
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Don't leak which articles you read to image hosts. ("no-referrer" would be
# stricter, but it makes browsers send `Origin: null` on our own form posts,
# which Django's CSRF check rightly rejects.)
SECURE_REFERRER_POLICY = "same-origin"

LANGUAGE_CODE = "en-gb"
TIME_ZONE = os.environ.get("TIME_ZONE", "Europe/Brussels")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Hashed filenames in production so nginx can cache /static/ aggressively.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
        )
    },
}

DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # OPML uploads

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"apps.feeds": {"handlers": ["console"], "level": "INFO"}},
}
