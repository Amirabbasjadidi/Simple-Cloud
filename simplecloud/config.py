"""Configuration: environment variables override values from the env file (/etc/simplecloud.env)."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_env_file(path):
    values = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip().strip('"').strip("'")
    except (FileNotFoundError, PermissionError):
        pass
    return values


def load_config():
    file_values = read_env_file(os.environ.get("SIMPLECLOUD_ENV_FILE", "/etc/simplecloud.env"))

    def get(name, default=None):
        return os.environ.get(name, file_values.get(name, default))

    def get_int(name, default):
        return int(get(name, default))

    def get_bool(name, default):
        return str(get(name, default)).lower() in ("1", "true", "yes", "on")

    secure = get_bool("COOKIE_SECURE", False)
    backup_folder = get("BACKUP_FOLDER", os.path.join(ROOT, "backups"))
    return {
        "SECRET_KEY": get("SECRET_KEY"),
        "DATABASE": get("DATABASE", os.path.join(ROOT, "database.db")),
        "UPLOAD_FOLDER": get("UPLOAD_FOLDER", os.path.join(ROOT, "uploads")),
        # Keep on the same filesystem as UPLOAD_FOLDER so migration backups can use hard links.
        "BACKUP_FOLDER": backup_folder,
        # Database-only backups (`flask backup --db-only`, the nightly timer); may be another disk or a NAS.
        "DB_BACKUP_FOLDER": get("DB_BACKUP_FOLDER", backup_folder),
        "PORT": get_int("PORT", 8000),
        "LOG_LEVEL": get("LOG_LEVEL", "INFO").upper(),
        # Interface language when the browser has not picked one: fa (Persian) or en.
        "DEFAULT_LANG": get("DEFAULT_LANG", "fa"),
        # Request body limit for uploads (bytes). Default 2 GiB.
        "MAX_CONTENT_LENGTH": get_int("MAX_CONTENT_LENGTH", 2 * 1024**3),
        # Size limit of one stored file (chunked uploads and URL fetches). Default 10 GiB.
        "MAX_FILE_SIZE": get_int("MAX_FILE_SIZE", 10 * 1024**3),
        "CHUNK_SIZE": get_int("CHUNK_SIZE", 8 * 1024**2),
        "UPLOAD_ABANDON_SECONDS": get_int("UPLOAD_ABANDON_SECONDS", 24 * 3600),
        # Default per-user quota in bytes for new users; 0 = unlimited.
        "DEFAULT_QUOTA_BYTES": get_int("DEFAULT_QUOTA_BYTES", 0),
        "MIN_PASSWORD_LENGTH": get_int("MIN_PASSWORD_LENGTH", 8),
        # Background URL fetch jobs
        "FETCH_WORKERS": get_int("FETCH_WORKERS", 3),
        "FETCH_MAX_ACTIVE_JOBS": get_int("FETCH_MAX_ACTIVE_JOBS", 5),
        # URL fetch limits
        "FETCH_MAX_BYTES": get_int("FETCH_MAX_BYTES", 2 * 1024**3),
        "FETCH_CONNECT_TIMEOUT": float(get("FETCH_CONNECT_TIMEOUT", 10)),
        "FETCH_READ_TIMEOUT": float(get("FETCH_READ_TIMEOUT", 30)),
        "FETCH_TOTAL_TIMEOUT": float(get("FETCH_TOTAL_TIMEOUT", 3600)),
        "FETCH_MAX_REDIRECTS": get_int("FETCH_MAX_REDIRECTS", 5),
        "FETCH_CA_BUNDLE": get("FETCH_CA_BUNDLE"),  # None -> certifi bundle
        # Login protection (shared across workers through SQLite)
        "LOGIN_MAX_FAILURES_USER": get_int("LOGIN_MAX_FAILURES_USER", 5),
        "LOGIN_MAX_FAILURES_IP": get_int("LOGIN_MAX_FAILURES_IP", 20),
        "LOGIN_LOCKOUT_SECONDS": get_int("LOGIN_LOCKOUT_SECONDS", 900),
        "CAPTCHA_TTL_SECONDS": get_int("CAPTCHA_TTL_SECONDS", 300),
        # Flask-Limiter: coarse per-worker limits; memory:// is per process.
        "RATELIMIT_STORAGE_URI": get("RATELIMIT_STORAGE_URI", "memory://"),
        "RATELIMIT_ENABLED": get_bool("RATELIMIT_ENABLED", True),
        # Number of reverse proxies in front of the app (1 behind Nginx).
        "PROXY_COUNT": get_int("PROXY_COUNT", 0),
        # Cookies
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": secure,
        "REMEMBER_COOKIE_HTTPONLY": True,
        "REMEMBER_COOKIE_SAMESITE": "Lax",
        "REMEMBER_COOKIE_SECURE": secure,
        "REMEMBER_COOKIE_DURATION": get_int("REMEMBER_DAYS", 30) * 86400,
        # CSRF token lives as long as the session so long-open pages keep working.
        "WTF_CSRF_TIME_LIMIT": None,
        "WTF_CSRF_HEADERS": ["X-CSRFToken"],
    }
