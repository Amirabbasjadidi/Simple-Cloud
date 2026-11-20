import secrets
import sqlite3

from flask import current_app, g
from werkzeug.security import check_password_hash, generate_password_hash


def connect(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    uploaded_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS captchas (
    token TEXT PRIMARY KEY,
    answer INTEGER NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS login_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    username TEXT,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_failures_ip ON login_failures(ip, ts);
CREATE INDEX IF NOT EXISTS idx_login_failures_user ON login_failures(username, ts);
"""


def init_db(app):
    conn = connect(app.config["DATABASE"])
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "must_change_password" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
        ensure_admin(conn, app)
        conn.commit()
    finally:
        conn.close()


def ensure_admin(conn, app):
    """First run only: create an admin with a random password. Flag legacy root/1234."""
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        password = secrets.token_urlsafe(12)
        cur = conn.execute(
            "INSERT OR IGNORE INTO users (username, password, must_change_password) VALUES (?, ?, 1)",
            ("admin", generate_password_hash(password)),
        )
        if cur.rowcount == 1:  # another gunicorn worker may have won the race
            print("=" * 60, flush=True)
            print(f"Initial admin created. Username: admin  Password: {password}", flush=True)
            print("You must change this password at first login.", flush=True)
            print("=" * 60, flush=True)
        return
    root = conn.execute("SELECT id, password FROM users WHERE username = 'root'").fetchone()
    if root and check_password_hash(root["password"], "1234"):
        conn.execute("UPDATE users SET must_change_password = 1 WHERE id = ?", (root["id"],))
        app.logger.warning("User 'root' still has the default password; a password change is now required.")
