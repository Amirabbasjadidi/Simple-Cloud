"""Background URL fetch jobs with progress polling.

Jobs run in a small thread pool inside each gunicorn worker. Progress and state live in the
`jobs` table, so any worker can answer polls. Each process heartbeats its active jobs; a job
whose heartbeat is older than STALE_AFTER seconds belonged to a worker that died (restart, crash,
OOM) and is marked failed, and its partial file deleted. This runs at startup and on every poll.
"""
import os
import secrets
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from . import quota, storage
from .db import connect, get_db, get_setting
from .events import log_event
from .fetch import FetchError, fetch_to_file
from .files import ApiError, add_file, owned_folder
from .i18n import gettext as _

bp = Blueprint("jobs", __name__)

HEARTBEAT_EVERY = 10
STALE_AFTER = 60
ACTIVE = ("queued", "running")

_state = {"pid": None, "executor": None, "worker": None}
_lock = threading.Lock()


class Cancelled(Exception):
    pass


@bp.errorhandler(ApiError)
def _api_error(e):
    return jsonify(success=False, error=str(e)), e.status


def _runtime(app):
    """Per-process executor + heartbeat thread (recreated after a fork)."""
    with _lock:
        if _state["pid"] != os.getpid():
            _state.update(pid=os.getpid(), worker=f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(4)}",
                          executor=ThreadPoolExecutor(max_workers=app.config["FETCH_WORKERS"],
                                                      thread_name_prefix="fetch"))
            threading.Thread(target=_heartbeat, args=(app, _state["worker"]), daemon=True,
                             name="fetch-heartbeat").start()
        return _state["executor"], _state["worker"]


def _heartbeat(app, worker):
    while True:
        time.sleep(HEARTBEAT_EVERY)
        try:
            conn = connect(app.config["DATABASE"])
            conn.execute("UPDATE jobs SET heartbeat = ? WHERE worker = ? AND status IN ('queued', 'running')",
                         (time.time(), worker))
            conn.commit()
            conn.close()
        except Exception:  # never let the heartbeat thread die
            app.logger.exception("job heartbeat failed")


def fail_stale(db, cfg):
    cutoff = time.time() - STALE_AFTER
    rows = db.execute("SELECT id, stored_name FROM jobs WHERE status IN ('queued', 'running') "
                      "AND COALESCE(heartbeat, created_at) < ?", (cutoff,)).fetchall()
    for r in rows:
        db.execute("UPDATE jobs SET status = 'failed', error = 'Interrupted: the server restarted during the "
                   "download.', stored_name = NULL, updated_at = ? WHERE id = ?", (time.time(), r["id"]))
    db.commit()
    for r in rows:
        if r["stored_name"]:
            storage.remove_quietly(storage.abs_path(r["stored_name"], cfg["UPLOAD_FOLDER"]))
    return len(rows)


def _run(app, job_id):
    with app.app_context():
        cfg = app.config
        db = connect(cfg["DATABASE"])
        try:
            job = db.execute("SELECT j.*, u.username, u.fetch_unlimited FROM jobs j JOIN users u ON u.id = j.user_id WHERE j.id = ?",
                             (job_id,)).fetchone()
            if job is None or job["status"] != "queued":
                return
            now = time.time()
            db.execute("UPDATE jobs SET status = 'running', heartbeat = ?, updated_at = ? WHERE id = ?",
                       (now, now, job_id))
            db.commit()
            limits = dict(cfg)
            left = quota.remaining(db, job["user_id"])
            admin_cap = None if job["fetch_unlimited"] else int(get_setting(db, "fetch_limit_bytes", 0))
            cap = min(x for x in (cfg["FETCH_MAX_BYTES"], cfg["MAX_FILE_SIZE"], admin_cap) if x)
            quota_bound = left is not None and left < cap  # the user's free space is the tighter limit
            limits["FETCH_MAX_BYTES"] = left if quota_bound else cap
            last = [0.0]

            def progress(done, total):
                if time.time() - last[0] < 0.5:
                    return
                last[0] = time.time()
                db.execute("UPDATE jobs SET done_bytes = ?, total_bytes = ?, updated_at = ? WHERE id = ?",
                           (done, total, last[0], job_id))
                db.commit()
                if db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == "cancelled":
                    raise Cancelled()

            hint = {}

            def write(path):
                db.execute("UPDATE jobs SET stored_name = ? WHERE id = ?",
                           (os.path.relpath(path, cfg["UPLOAD_FOLDER"]).replace(os.sep, "/"), job_id))
                db.commit()
                hint["name"] = fetch_to_file(job["url"], path, limits, progress)[0] or "download"

            try:
                file_id, name = add_file(db, job["user_id"], lambda: hint["name"], write, folder_id=job["folder_id"])
            except Cancelled:
                db.execute("UPDATE jobs SET stored_name = NULL, updated_at = ? WHERE id = ?", (time.time(), job_id))
                db.commit()
                log_event("fetch_cancelled", user=job["username"], url=job["url"])
                return
            except (FetchError, quota.QuotaError) as e:
                if quota_bound and "larger" in str(e):
                    e = quota.QuotaError("Not enough storage space: this would exceed your quota.")
                _finish(db, job_id, "failed", error=str(e))
                log_event("fetch_failed", user=job["username"], url=job["url"], error=e)
                return
            size = db.execute("SELECT size FROM files WHERE id = ?", (file_id,)).fetchone()[0]
            _finish(db, job_id, "done", file_id=file_id, name=name, done_bytes=size)
            log_event("fetch_ok", user=job["username"], file_id=file_id, name=name, url=job["url"])
        except Exception:
            app.logger.exception("fetch job %s crashed", job_id)
            _finish(db, job_id, "failed", error="Internal error.")
        finally:
            db.close()


def _finish(db, job_id, status, **cols):
    cols.update(status=status, stored_name=None, updated_at=time.time())
    sets = ", ".join(f"{k} = ?" for k in cols)
    db.execute(f"UPDATE jobs SET {sets} WHERE id = ? AND status != 'cancelled'", (*cols.values(), job_id))
    db.commit()


def job_json(r):
    return {"id": r["id"], "url": r["url"], "status": r["status"], "done_bytes": r["done_bytes"],
            "total_bytes": r["total_bytes"], "file_id": r["file_id"], "name": r["name"], "error": r["error"],
            "folder_id": r["folder_id"], "created_at": r["created_at"]}


def _owned_job(db, job_id):
    row = db.execute("SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, current_user.id)).fetchone()
    if row is None:
        raise ApiError("Job not found.", 404)
    return row


@bp.route("/api/fetch", methods=["POST"])
@login_required
def create():
    db = get_db()
    cfg = current_app.config
    data = request.get_json(silent=True) or {}
    urls = data.get("urls") or ([data["url"]] if data.get("url") else [])
    urls = [str(u).strip() for u in urls if str(u).strip()]
    if not urls:
        raise ApiError("No URL provided.")
    for u in urls:
        if urlsplit(u).scheme not in ("http", "https") or not urlsplit(u).hostname:
            raise ApiError(f"Not a valid http(s) URL: {u}")
    folder = owned_folder(db, current_user.id, data.get("folder_id"))
    fail_stale(db, cfg)
    active = db.execute("SELECT COUNT(*) FROM jobs WHERE user_id = ? AND status IN ('queued', 'running')",
                        (current_user.id,)).fetchone()[0]
    if active + len(urls) > cfg["FETCH_MAX_ACTIVE_JOBS"]:
        raise ApiError(_("At most %(n)s downloads can run at once.", n=cfg['FETCH_MAX_ACTIVE_JOBS']), 429)

    executor, worker = _runtime(current_app._get_current_object())
    now = time.time()
    created = []
    for u in urls:
        job_id = secrets.token_urlsafe(12)
        db.execute("INSERT INTO jobs (id, user_id, folder_id, url, status, worker, heartbeat, created_at, updated_at) "
                   "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
                   (job_id, current_user.id, folder["id"] if folder else None, u, worker, now, now, now))
        created.append(job_id)
    db.commit()
    app = current_app._get_current_object()
    for job_id in created:
        executor.submit(_run, app, job_id)
    rows = [db.execute("SELECT * FROM jobs WHERE id = ?", (j,)).fetchone() for j in created]
    return jsonify(success=True, jobs=[job_json(r) for r in rows])


@bp.route("/api/fetch", methods=["GET"])
@login_required
def list_jobs():
    db = get_db()
    fail_stale(db, current_app.config)
    rows = db.execute("SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
                      (current_user.id,)).fetchall()
    return jsonify(jobs=[job_json(r) for r in rows])


@bp.route("/api/fetch/<job_id>", methods=["GET"])
@login_required
def get_job(job_id):
    db = get_db()
    fail_stale(db, current_app.config)
    return jsonify(success=True, job=job_json(_owned_job(db, job_id)))


@bp.route("/api/fetch/<job_id>", methods=["DELETE"])
@login_required
def cancel_or_dismiss(job_id):
    """Cancel an active job, or remove a finished one from the list."""
    db = get_db()
    job = _owned_job(db, job_id)
    if job["status"] in ACTIVE:
        db.execute("UPDATE jobs SET status = 'cancelled', error = 'Cancelled.', updated_at = ? WHERE id = ?",
                   (time.time(), job_id))
    else:
        db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    db.commit()
    return jsonify(success=True)
