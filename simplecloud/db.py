import secrets
import sqlite3

from flask import current_app, g
from werkzeug.security import check_password_hash, generate_password_hash

# Bump together with a new step in migrate.MIGRATIONS. tests/test_migration.py checks that a
# fresh database (SCHEMA below) and a migrated legacy database end up with the same tables.
LATEST_VERSION = 6

# Tables added in schema v3 (shared by the fresh schema and migrate_v3).
V3_TABLES = [
    """CREATE TABLE uploads (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        folder_id INTEGER,
        name TEXT NOT NULL,
        total_size INTEGER NOT NULL,
        received INTEGER NOT NULL DEFAULT 0,
        stored_name TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )""",
    """CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        folder_id INTEGER,
        url TEXT NOT NULL,
        status TEXT NOT NULL,
        done_bytes INTEGER NOT NULL DEFAULT 0,
        total_bytes INTEGER,
        file_id INTEGER,
        name TEXT,
        error TEXT,
        stored_name TEXT,
        worker TEXT,
        heartbeat REAL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )""",
    "CREATE INDEX idx_jobs_user ON jobs(user_id, created_at)",
    """CREATE TABLE shares (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL REFERENCES users(id),
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        created_at REAL NOT NULL,
        expires_at REAL,
        password_hash TEXT,
        max_downloads INTEGER,
        download_count INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX idx_shares_file ON shares(file_id)",
]

# Tables added in schema v4 (shared by the fresh schema and migrate_v4).
V4_TABLES = [
    # A counted share download opens a grant for that client (cookie token or IP) so its range,
    # parallel-segment and retry requests are neither refused nor counted again.
    """CREATE TABLE share_grants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        share_id INTEGER NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL UNIQUE,
        ip TEXT NOT NULL,
        expires_at REAL NOT NULL
    )""",
    "CREATE INDEX idx_share_grants_share ON share_grants(share_id, expires_at)",
    # "Back up now" runs from the admin dashboard (one at a time across workers).
    """CREATE TABLE backup_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        status TEXT NOT NULL,
        started_at REAL NOT NULL,
        finished_at REAL,
        started_by TEXT,
        path TEXT,
        error TEXT
    )""",
]

# Schema v5 steps (run by both the fresh schema and migrate_v5).
V5_STEPS = [
    # Admin settings changed from the dashboard (key -> text value).
    "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)",
    # Exempt from the admin's URL-download size limit (the user's quota still applies).
    "ALTER TABLE users ADD COLUMN fetch_unlimited INTEGER NOT NULL DEFAULT 0",
    # Share link usable only by signed-in users.
    "ALTER TABLE shares ADD COLUMN login_required INTEGER NOT NULL DEFAULT 0",
]

# Schema v6 steps (run by both the fresh schema and migrate_v6, with foreign keys off: the
# shares table is rebuilt so a share can point to a file or a folder).
V6_STEPS = [
    # Trash: deleted files keep their row (and count toward the quota) until purged.
    "ALTER TABLE files ADD COLUMN deleted_at REAL",
    "ALTER TABLE files ADD COLUMN trash_folder_id INTEGER",  # where to restore to (no FK: may be gone)
    "ALTER TABLE files ADD COLUMN trash_path TEXT",          # that folder's path, for display
    # Files that arrived through an upload link: which link, and the signed-in uploader (if any).
    "ALTER TABLE files ADD COLUMN upload_link_id INTEGER",
    "ALTER TABLE files ADD COLUMN uploaded_by TEXT",
    "CREATE INDEX idx_files_trash ON files(user_id, deleted_at)",
    "ALTER TABLE uploads ADD COLUMN link_id INTEGER",
    "ALTER TABLE uploads ADD COLUMN uploader TEXT",
    """CREATE TABLE shares_v6 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL REFERENCES users(id),
        file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
        folder_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
        created_at REAL NOT NULL,
        expires_at REAL,
        password_hash TEXT,
        max_downloads INTEGER,
        download_count INTEGER NOT NULL DEFAULT 0,
        login_required INTEGER NOT NULL DEFAULT 0,
        CHECK ((file_id IS NULL) != (folder_id IS NULL))
    )""",
    "INSERT INTO shares_v6 (id, token, user_id, file_id, created_at, expires_at, password_hash, max_downloads, "
    "download_count, login_required) SELECT id, token, user_id, file_id, created_at, expires_at, password_hash, "
    "max_downloads, download_count, login_required FROM shares",
    "DROP TABLE shares",
    "ALTER TABLE shares_v6 RENAME TO shares",
    "CREATE INDEX idx_shares_file ON shares(file_id)",
    "CREATE INDEX idx_shares_folder ON shares(folder_id)",
    # Upload links: others upload into one of the owner's folders (NULL = root).
    """CREATE TABLE upload_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL REFERENCES users(id),
        folder_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
        created_at REAL NOT NULL,
        expires_at REAL,
        password_hash TEXT,
        login_required INTEGER NOT NULL DEFAULT 0,
        max_files INTEGER,
        file_count INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX idx_upload_links_user ON upload_links(user_id)",
    # Activity log shown in the admin dashboard (also written to the service log).
    """CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        event TEXT NOT NULL,
        username TEXT,
        ip TEXT,
        details TEXT
    )""",
    "CREATE INDEX idx_events_ts ON events(ts)",
    "CREATE INDEX idx_events_user ON events(username, ts)",
]

SCHEMA = [
    """CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        is_admin INTEGER NOT NULL DEFAULT 0,
        disabled INTEGER NOT NULL DEFAULT 0,
        session_version INTEGER NOT NULL DEFAULT 0,
        quota_bytes INTEGER,
        totp_secret TEXT,
        totp_pending_secret TEXT,
        totp_last_step INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE folders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        parent_id INTEGER REFERENCES folders(id),
        name TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX idx_folders_parent ON folders(user_id, parent_id)",
    """CREATE TABLE files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        name TEXT NOT NULL,
        stored_name TEXT NOT NULL UNIQUE,
        size INTEGER NOT NULL,
        mime_type TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        folder_id INTEGER REFERENCES folders(id)
    )""",
    "CREATE INDEX idx_files_user ON files(user_id)",
    "CREATE INDEX idx_files_folder ON files(user_id, folder_id)",
    *V3_TABLES,
    *V4_TABLES,
    """CREATE TABLE captchas (
        token TEXT PRIMARY KEY,
        answer INTEGER NOT NULL,
        expires_at REAL NOT NULL
    )""",
    """CREATE TABLE login_failures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ip TEXT NOT NULL,
        username TEXT,
        ts REAL NOT NULL
    )""",
    "CREATE INDEX idx_login_failures_ip ON login_failures(ip, ts)",
    "CREATE INDEX idx_login_failures_user ON login_failures(username, ts)",
    *V5_STEPS,
    *V6_STEPS,
]


def connect(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_setting(db, key, default=None):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return default if row is None else row[0]


def set_setting(db, key, value):
    if value is None:
        db.execute("DELETE FROM settings WHERE key = ?", (key,))
    else:
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                   (key, str(value)))


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def table_exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def schema_version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def init_db(app):
    """Create the schema on a fresh database. Returns True if the schema is current.

    An existing database with an older schema is never changed here: run `flask migrate`.
    """
    conn = connect(app.config["DATABASE"])
    conn.isolation_level = None  # explicit transactions
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("BEGIN IMMEDIATE")  # serializes concurrent gunicorn workers on first start
        if not table_exists(conn, "users"):
            for stmt in SCHEMA:
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {LATEST_VERSION}")
        current = schema_version(conn) == LATEST_VERSION
        if current:
            ensure_admin(conn, app.logger)
        conn.execute("COMMIT")
        return current
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def ensure_admin(conn, logger):
    """First run only: create an admin with a random password. Flag legacy root/1234."""
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        password = secrets.token_urlsafe(12)
        conn.execute("INSERT INTO users (username, password, must_change_password, is_admin) VALUES (?, ?, 1, 1)",
                     ("admin", generate_password_hash(password)))
        print("=" * 60, flush=True)
        print(f"Initial admin created. Username: admin  Password: {password}", flush=True)
        print("You must change this password at first login.", flush=True)
        print("=" * 60, flush=True)
        return
    root = conn.execute("SELECT id, password FROM users WHERE username = 'root'").fetchone()
    if root and check_password_hash(root["password"], "1234"):
        conn.execute("UPDATE users SET must_change_password = 1 WHERE id = ?", (root["id"],))
        logger.warning("User 'root' still has the default password; a password change is now required.")
