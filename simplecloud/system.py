"""Admin "System / Storage" dashboard: disk and per-user usage, quota over-allocation, largest files,
database backups, and a one-at-a-time "Back up now" job.

Warnings are returned as codes with numbers; the page words them (in Persian or English).
"""
import glob
import os
import shutil
import threading
import time

from . import backup
from .db import connect

_cache = {"at": 0.0, "data": None}
_cache_lock = threading.Lock()
RUN_STALE_SECONDS = 1800  # a "running" backup older than this died with its worker
TIMER_LINK = "/etc/systemd/system/timers.target.wants/simplecloud-backup.timer"


def _pct(part, whole):
    return round(100 * part / whole, 1) if whole else None


def storage_stats(db, cfg):
    """Disk, per-user and largest-file figures. Cached per worker for STATS_CACHE_SECONDS,
    since summing and sorting all files gets slow with many of them."""
    with _cache_lock:
        if _cache["data"] is not None and time.time() - _cache["at"] < cfg["STATS_CACHE_SECONDS"]:
            return _cache["data"]
    disk = shutil.disk_usage(cfg["UPLOAD_FOLDER"])
    rows = db.execute(
        "SELECT u.id, u.username, u.quota_bytes, u.disabled, COUNT(f.id) AS file_count, "
        "COALESCE(SUM(f.size), 0) AS used FROM users u LEFT JOIN files f ON f.user_id = u.id "
        "GROUP BY u.id").fetchall()
    cloud = sum(r["used"] for r in rows)
    users = [{
        "id": r["id"], "username": r["username"], "disabled": bool(r["disabled"]),
        "used": r["used"], "file_count": r["file_count"], "quota": r["quota_bytes"] or None,
        "pct_quota": _pct(r["used"], r["quota_bytes"]) if r["quota_bytes"] else None,
        "pct_disk": _pct(r["used"], disk.total), "pct_cloud": _pct(r["used"], cloud),
    } for r in rows]
    quota_total = sum(u["quota"] or 0 for u in users)
    unlimited = sorted(u["username"] for u in users if not u["quota"] and not u["disabled"])
    available = disk.free + cloud  # what users could fill: free space plus what they already hold
    largest = [dict(r) for r in db.execute(
        "SELECT f.name, f.size, f.created_at, u.username AS owner FROM files f JOIN users u ON u.id = f.user_id "
        "ORDER BY f.size DESC, f.id LIMIT 20")]
    warnings = []
    if quota_total > available:
        warnings.append({"code": "over_allocated", "allocated": quota_total, "available": available,
                         "capacity": disk.total})
    if unlimited:
        warnings.append({"code": "unlimited_users", "users": unlimited})
    free_pct = _pct(disk.free, disk.total) or 0
    if free_pct < cfg["LOW_DISK_PERCENT"]:
        warnings.append({"code": "low_disk", "free": disk.free, "free_pct": free_pct,
                         "threshold": cfg["LOW_DISK_PERCENT"]})
    data = {
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free, "cloud": cloud,
                 "other": max(0, disk.used - cloud), "free_pct": free_pct},
        "users": users,
        "allocation": {"quota_total": quota_total, "capacity": disk.total, "available": available,
                       "unlimited_users": unlimited, "over_capacity": quota_total > disk.total,
                       "over_available": quota_total > available},
        "largest": largest,
        "warnings": warnings,
        "generated_at": time.time(),
    }
    with _cache_lock:
        _cache.update(at=time.time(), data=data)
    return data


def clear_cache():
    with _cache_lock:
        _cache.update(at=0.0, data=None)


def _timer_enabled():
    """True/False from the systemd enable symlink (a read-only file check); None when this is not a
    systemd install (Docker, development) and it cannot be told."""
    if not os.path.isdir("/etc/systemd/system"):
        return None
    return os.path.lexists(TIMER_LINK)


def backup_status(cfg):
    folder = cfg["DB_BACKUP_FOLDER"]
    archives = []
    for path in glob.glob(os.path.join(glob.escape(folder), f"{backup.DB_PREFIX}[0-9]*{backup.SUFFIX}")):
        try:
            st = os.stat(path)
        except OSError:
            continue  # removed by retention meanwhile
        archives.append({"name": os.path.basename(path), "size": st.st_size, "mtime": st.st_mtime})
    archives.sort(key=lambda a: a["mtime"], reverse=True)
    exists = os.path.isdir(folder)
    writable = os.access(folder if exists else os.path.dirname(folder.rstrip("/\\")) or ".", os.W_OK)
    last = archives[0]["mtime"] if archives else None
    age_hours = round((time.time() - last) / 3600, 1) if last else None
    warnings = []
    if not writable:
        warnings.append({"code": "backup_not_writable", "folder": folder})
    if last is None:
        warnings.append({"code": "backup_missing"})
    elif age_hours > cfg["BACKUP_MAX_AGE_HOURS"]:
        warnings.append({"code": "backup_old", "age_hours": age_hours, "limit": cfg["BACKUP_MAX_AGE_HOURS"]})
    timer = _timer_enabled()
    if timer is False:
        warnings.append({"code": "backup_timer_off"})
    return {"folder": folder, "writable": writable, "keep": cfg["BACKUP_KEEP"], "count": len(archives),
            "archives": archives, "last": last, "age_hours": age_hours, "timer": timer, "warnings": warnings}


# ---------------------------------------------------------------- "Back up now"

def _run_json(r):
    return None if r is None else {k: r[k] for k in r.keys()}


def latest_run(db):
    db.execute("UPDATE backup_runs SET status = 'failed', finished_at = ?, error = 'interrupted' "
               "WHERE status = 'running' AND started_at < ?", (time.time(), time.time() - RUN_STALE_SECONDS))
    db.commit()
    return _run_json(db.execute("SELECT * FROM backup_runs ORDER BY id DESC LIMIT 1").fetchone())


class BackupBusy(Exception):
    pass


def start_backup(db, cfg, admin_name, logger):
    """Record a run and start the database backup in a background thread. One at a time across
    all workers: the check and the insert happen under SQLite's write lock."""
    now = time.time()
    db.execute("BEGIN IMMEDIATE")
    try:
        busy = db.execute("SELECT * FROM backup_runs WHERE status = 'running' AND started_at >= ?",
                          (now - RUN_STALE_SECONDS,)).fetchone()
        if busy:
            db.execute("ROLLBACK")
            raise BackupBusy(_run_json(busy))
        run_id = db.execute("INSERT INTO backup_runs (status, started_at, started_by) VALUES ('running', ?, ?)",
                            (now, admin_name)).lastrowid
        db.execute("COMMIT")
    except BackupBusy:
        raise
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    cfg = dict(cfg)
    threading.Thread(target=_run, args=(cfg, run_id, logger), name=f"backup-{run_id}", daemon=True).start()
    return run_id


def _run(cfg, run_id, logger):
    status, path, error = "done", None, None
    try:
        path = backup.create(cfg, keep=cfg["BACKUP_KEEP"], db_only=True, echo=lambda _m: None)
    except Exception as e:  # reported on the dashboard
        status, error = "failed", f"{e.__class__.__name__}: {e}"
        logger.error("event=admin_backup_failed run=%s error=%s", run_id, error)
    conn = connect(cfg["DATABASE"])
    try:
        conn.execute("UPDATE backup_runs SET status = ?, finished_at = ?, path = ?, error = ? WHERE id = ?",
                     (status, time.time(), path, error, run_id))
        conn.commit()
    finally:
        conn.close()
    if status == "done":
        logger.info("event=admin_backup_done run=%s path=%s", run_id, path)
