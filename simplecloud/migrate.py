"""Database + storage migrations, run with `flask migrate [--dry-run]`.

Every step is idempotent and resumable: planned work is persisted in the database before any file
is touched, each file move is committed individually, and a crash at any point can be re-run.
"""
import datetime
import os
import shutil
import sqlite3

from .db import LATEST_VERSION, connect, ensure_admin, schema_version, table_exists
from .storage import abs_path, guess_mime, new_stored_name


class MigrationError(Exception):
    pass


def columns(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


# ---------------------------------------------------------------- backup

def backup(cfg, echo):
    """Timestamped backup of the DB (SQLite online backup) and uploads/.

    Upload files are hard-linked when possible: the migration only renames files, never rewrites
    them, so a hard link keeps the original bytes at zero extra disk cost. Falls back to copying,
    after checking there is enough free space.
    """
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(cfg["BACKUP_FOLDER"], stamp)
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(cfg["BACKUP_FOLDER"], f"{stamp}-{n}")
        n += 1
    os.makedirs(dest)

    src = sqlite3.connect(cfg["DATABASE"])
    dst = sqlite3.connect(os.path.join(dest, "database.db"))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    upload_root = cfg["UPLOAD_FOLDER"]
    files = []
    for dirpath, _dirs, names in os.walk(upload_root):
        files += [os.path.join(dirpath, n) for n in names]
    copying = False
    for i, path in enumerate(files):
        target = os.path.join(dest, "uploads", os.path.relpath(path, upload_root))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if not copying:
            try:
                os.link(path, target)
                continue
            except OSError:
                copying = True
                need = sum(os.path.getsize(p) for p in files[i:])
                free = shutil.disk_usage(dest).free
                if need * 1.05 > free:
                    raise MigrationError(
                        f"Not enough disk space to back up uploads ({need} bytes needed, {free} free). "
                        f"Free some space or back up {upload_root} manually, then re-run.")
        shutil.copy2(path, target)
    echo(f"Backup written to {dest} ({len(files)} files, {'copied' if copying else 'hard-linked'})")
    return dest


# ---------------------------------------------------------------- v2: user_id FK + UUID storage

FILES_V2 = """CREATE TABLE IF NOT EXISTS files_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    stored_name TEXT NOT NULL UNIQUE,
    size INTEGER NOT NULL,
    mime_type TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)"""

MOVES = """CREATE TABLE IF NOT EXISTS migration_moves (
    legacy_id INTEGER PRIMARY KEY,
    user_id INTEGER,
    name TEXT NOT NULL,
    src TEXT NOT NULL,
    stored_name TEXT,
    status TEXT NOT NULL  -- pending | done | orphan | missing
)"""

AUX_TABLES = [
    "CREATE TABLE IF NOT EXISTS captchas (token TEXT PRIMARY KEY, answer INTEGER NOT NULL, expires_at REAL NOT NULL)",
    """CREATE TABLE IF NOT EXISTS login_failures (id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT NOT NULL,
       username TEXT, ts REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_login_failures_ip ON login_failures(ip, ts)",
    "CREATE INDEX IF NOT EXISTS idx_login_failures_user ON login_failures(username, ts)",
]


def _plan_v2(conn, cfg):
    users = {r["username"]: r["id"] for r in conn.execute("SELECT id, username FROM users")}
    plan = []
    for row in conn.execute("SELECT id, name, path, uploaded_by FROM files ORDER BY id"):
        src = os.path.join(cfg["UPLOAD_FOLDER"], os.path.basename(row["path"] or row["name"]))
        uid = users.get(row["uploaded_by"])
        if uid is None:
            status = "orphan"
        elif not os.path.isfile(src):
            status = "missing"
        else:
            status = "pending"
        plan.append({"legacy_id": row["id"], "user_id": uid, "name": row["name"], "src": src,
                     "stored_name": new_stored_name(uid) if status == "pending" else None,
                     "status": status, "owner": row["uploaded_by"]})
    return plan


def migrate_v2(conn, cfg, dry_run, echo):
    legacy = table_exists(conn, "files") and "uploaded_by" in columns(conn, "files")
    if not legacy:
        raise MigrationError("Unexpected schema: no legacy 'files' table with 'uploaded_by'.")

    if table_exists(conn, "migration_moves"):
        plan = [dict(r) for r in conn.execute("SELECT * FROM migration_moves ORDER BY legacy_id")]
        echo("Resuming an interrupted migration.")
    else:
        plan = _plan_v2(conn, cfg)

    if dry_run:
        echo("DB changes:")
        echo("  - add users.must_change_password, tables captchas/login_failures (if missing)")
        echo("  - new files table: user_id FK, stored_name, size, mime_type, created_at")
        echo("  - old files table kept as legacy_files (nothing is dropped)")
        echo("File moves:")
        for p in plan:
            if p["status"] in ("pending", "done"):
                echo(f"  [{p['status']}] #{p['legacy_id']} {p['src']} -> "
                     f"{abs_path(p['stored_name'], cfg['UPLOAD_FOLDER'])}  (name: {p['name']})")
            else:
                why = "owner no longer exists" if p["status"] == "orphan" else "file not found on disk"
                echo(f"  [skip: {why}] #{p['legacy_id']} {p['src']} - left untouched, kept in legacy_files")
        referenced = {os.path.normcase(os.path.abspath(p["src"])) for p in plan}
        for name in sorted(os.listdir(cfg["UPLOAD_FOLDER"])) if os.path.isdir(cfg["UPLOAD_FOLDER"]) else []:
            path = os.path.join(cfg["UPLOAD_FOLDER"], name)
            if os.path.isfile(path) and os.path.normcase(os.path.abspath(path)) not in referenced:
                echo(f"  [stray: not in DB, left untouched] {path}")
        return

    # 1) persist the plan (one transaction) before touching any file
    conn.execute("BEGIN IMMEDIATE")
    cols = columns(conn, "users")
    if "must_change_password" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
    for stmt in AUX_TABLES:
        conn.execute(stmt)
    conn.execute(FILES_V2)
    if not table_exists(conn, "migration_moves"):
        conn.execute(MOVES)
        conn.executemany(
            "INSERT INTO migration_moves (legacy_id, user_id, name, src, stored_name, status) "
            "VALUES (:legacy_id, :user_id, :name, :src, :stored_name, :status)", plan)
    conn.execute("COMMIT")

    # 2) move files one by one; each move + its row commit together, so re-running is safe
    moved = 0
    for p in conn.execute("SELECT * FROM migration_moves WHERE status = 'pending'").fetchall():
        dst = abs_path(p["stored_name"], cfg["UPLOAD_FOLDER"])
        if os.path.isfile(p["src"]) and not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(p["src"], dst)
        elif not os.path.isfile(dst):  # neither place: vanished since planning
            conn.execute("UPDATE migration_moves SET status = 'missing' WHERE legacy_id = ?", (p["legacy_id"],))
            conn.commit()
            continue
        stat = os.stat(dst)
        created = datetime.datetime.fromtimestamp(stat.st_mtime, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR IGNORE INTO files_v2 (id, user_id, name, stored_name, size, mime_type, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (p["legacy_id"], p["user_id"], p["name"], p["stored_name"], stat.st_size, guess_mime(p["name"]), created))
        conn.execute("UPDATE migration_moves SET status = 'done' WHERE legacy_id = ?", (p["legacy_id"],))
        conn.execute("COMMIT")
        moved += 1

    # 3) swap tables
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("ALTER TABLE files RENAME TO legacy_files")
    conn.execute("ALTER TABLE files_v2 RENAME TO files")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_user ON files(user_id)")
    skipped = conn.execute("SELECT legacy_id, src, status FROM migration_moves WHERE status != 'done'").fetchall()
    conn.execute("DROP TABLE migration_moves")
    conn.execute("PRAGMA user_version = 2")
    conn.execute("COMMIT")
    echo(f"v2: moved {moved} file(s) to UUID storage.")
    for s in skipped:
        echo(f"  skipped #{s['legacy_id']} ({s['status']}): {s['src']} - left untouched, row kept in legacy_files")


MIGRATIONS = {2: migrate_v2}


def run(cfg, dry_run=False, echo=print, logger=None):
    conn = connect(cfg["DATABASE"])
    conn.isolation_level = None
    try:
        version = schema_version(conn)
        if version == 0 and not table_exists(conn, "users"):
            echo("Fresh database: nothing to migrate (the app creates the schema on start).")
            return
        if version == 0:
            version = 1  # original Simple Cloud schema
        if version >= LATEST_VERSION:
            echo(f"Database is up to date (schema v{version}).")
            return
        echo(f"Schema v{version} -> v{LATEST_VERSION}{' (dry run, nothing will change)' if dry_run else ''}")
        if not dry_run:
            backup(cfg, echo)
        for target in range(version + 1, LATEST_VERSION + 1):
            MIGRATIONS[target](conn, cfg, dry_run, echo)
        if not dry_run:
            conn.execute("BEGIN IMMEDIATE")
            ensure_admin(conn, logger or _PrintLogger(echo))
            conn.execute("COMMIT")
            echo("Migration complete.")
    finally:
        conn.close()


class _PrintLogger:
    def __init__(self, echo):
        self.warning = echo
