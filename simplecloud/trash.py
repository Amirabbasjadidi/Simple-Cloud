"""Trash: deleted files wait here for TRASH_DAYS, then are purged. They keep counting toward the
owner's quota until then (quota.usage sums every file row), so deleting frees no space until the
file leaves the trash.

GET    /api/trash                      list
POST   /api/trash/<id>/restore         back to its folder (or the root if that folder is gone)
DELETE /api/trash/<id>                 delete permanently
DELETE /api/trash                      empty the trash
"""
import time

from flask import Blueprint, current_app, jsonify, render_template
from flask_login import current_user, login_required

from . import storage
from .db import get_db
from .events import log_event
from .files import ApiError, destroy_files

bp = Blueprint("trash", __name__)

HOUSEKEEPING_SECONDS = 600
_last_housekeeping = [0.0]


@bp.errorhandler(ApiError)
def _api_error(e):
    return jsonify(success=False, error=str(e)), e.status


def purge_trash(db, cfg, now=None):
    """Delete files that have been in the trash longer than TRASH_DAYS. Returns the count."""
    cutoff = (now or time.time()) - cfg["TRASH_DAYS"] * 86400
    rows = db.execute("SELECT id, stored_name FROM files WHERE deleted_at IS NOT NULL AND deleted_at < ?",
                      (cutoff,)).fetchall()
    destroy_files(db, rows)
    return len(rows)


def housekeeping(db, cfg):
    """Purge the trash and old activity-log rows: at startup and at most every 10 minutes per worker."""
    _last_housekeeping[0] = time.time()
    purged = purge_trash(db, cfg)
    db.execute("DELETE FROM events WHERE ts < ?", (time.time() - cfg["EVENTS_KEEP_DAYS"] * 86400,))
    db.commit()
    if purged:
        current_app.logger.info("event=trash_purge files=%s", purged)


def maybe_housekeeping():
    if time.time() - _last_housekeeping[0] > HOUSEKEEPING_SECONDS:
        housekeeping(get_db(), current_app.config)


def _trashed(file_id=None):
    db = get_db()
    if file_id is None:
        return db.execute("SELECT * FROM files WHERE user_id = ? AND deleted_at IS NOT NULL ORDER BY deleted_at DESC",
                          (current_user.id,)).fetchall()
    row = db.execute("SELECT * FROM files WHERE id = ? AND user_id = ? AND deleted_at IS NOT NULL",
                     (file_id, current_user.id)).fetchone()
    if row is None:
        raise ApiError("File not found in the trash.", 404)
    return row


@bp.route("/trash")
@login_required
def trash_page():
    return render_template("trash.html", days=current_app.config["TRASH_DAYS"])


@bp.route("/api/trash")
@login_required
def list_trash():
    keep = current_app.config["TRASH_DAYS"] * 86400
    rows = _trashed()
    return jsonify(files=[{
        "id": r["id"], "name": r["name"], "size": r["size"], "mime_type": r["mime_type"],
        "deleted_at": r["deleted_at"], "purge_at": r["deleted_at"] + keep, "folder": r["trash_path"] or "",
    } for r in rows], total_size=sum(r["size"] for r in rows))


@bp.route("/api/trash/<int:file_id>/restore", methods=["POST"])
@login_required
def restore(file_id):
    db = get_db()
    row = _trashed(file_id)
    folder_id = row["trash_folder_id"]
    if folder_id and not db.execute("SELECT 1 FROM folders WHERE id = ? AND user_id = ?",
                                    (folder_id, current_user.id)).fetchone():
        folder_id = None  # its folder was deleted meanwhile
    name = storage.unique_name(db, current_user.id, row["name"], folder_id)
    db.execute("UPDATE files SET deleted_at = NULL, trash_folder_id = NULL, trash_path = NULL, folder_id = ?, "
               "name = ? WHERE id = ?", (folder_id, name, file_id))
    db.commit()
    log_event("trash_restore", user=current_user.username, file_id=file_id, name=name)
    return jsonify(success=True, folder_id=folder_id, name=name)


@bp.route("/api/trash/<int:file_id>", methods=["DELETE"])
@login_required
def delete_forever(file_id):
    row = _trashed(file_id)
    destroy_files(get_db(), [row])
    log_event("trash_delete", user=current_user.username, file_id=file_id, name=row["name"])
    return jsonify(success=True)


@bp.route("/api/trash", methods=["DELETE"])
@login_required
def empty_trash():
    rows = _trashed()
    destroy_files(get_db(), rows)
    log_event("trash_empty", user=current_user.username, files=len(rows))
    return jsonify(success=True, deleted=len(rows))
