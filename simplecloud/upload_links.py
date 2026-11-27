"""Upload links: let others upload into one of your folders, with optional expiry, password,
sign-in requirement and file limit. Files count toward the owner's quota and record which link
brought them and, when the uploader is signed in, their username.

Owner:  POST /api/upload-links, GET /api/upload-links, DELETE /api/upload-links/<id>
Public: GET /r/<token> (page), POST /r/<token> (password),
        POST /r/<token>/uploads, GET|PUT|DELETE /r/<token>/uploads/<upload_id> (chunked, resumable)
"""
import secrets
import time

from flask import Blueprint, jsonify, render_template, request, session, url_for
from flask_login import current_user, login_required
from werkzeug.security import check_password_hash, generate_password_hash

from . import limiter, storage
from .db import get_db
from .events import log_event
from .files import ApiError, folder_path, owned_folder
from .shares import MAX_EXPIRY_HOURS, _positive_int
from .uploads import put_chunk_for, start_upload

bp = Blueprint("upload_links", __name__)


@bp.errorhandler(ApiError)
def _api_error(e):
    return jsonify(success=False, error=str(e)), e.status


def _state(r):
    if r["expires_at"] is not None and r["expires_at"] <= time.time():
        return "expired"
    if r["max_files"] is not None and r["file_count"] >= r["max_files"]:
        return "full"
    return "ok"


def link_json(db, r):
    return {
        "id": r["id"], "url": url_for("upload_links.public_page", token=r["token"], _external=True),
        "folder_id": r["folder_id"], "folder": folder_path(db, r["folder_id"]),
        "created_at": r["created_at"], "expires_at": r["expires_at"], "has_password": bool(r["password_hash"]),
        "login_required": bool(r["login_required"]), "max_files": r["max_files"], "file_count": r["file_count"],
        "expired": _state(r) != "ok",
    }


# ---------------------------------------------------------------- owner side

@bp.route("/api/upload-links", methods=["POST"])
@login_required
def create_link():
    db = get_db()
    data = request.get_json(silent=True) or {}
    folder = owned_folder(db, current_user.id, data.get("folder_id"))
    hours = _positive_int(data.get("expires_hours"), "expiry", MAX_EXPIRY_HOURS)
    max_files = _positive_int(data.get("max_files"), "file limit", 1_000_000)
    password = str(data.get("password") or "")
    now = time.time()
    cur = db.execute(
        "INSERT INTO upload_links (token, user_id, folder_id, created_at, expires_at, password_hash, login_required, "
        "max_files) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (secrets.token_urlsafe(24), current_user.id, folder["id"] if folder else None, now,
         now + hours * 3600 if hours else None, generate_password_hash(password) if password else None,
         int(bool(data.get("login_required"))), int(max_files) if max_files else None))
    db.commit()
    log_event("upload_link_create", user=current_user.username, link_id=cur.lastrowid,
              folder_id=folder["id"] if folder else None, expires_hours=hours, password=bool(password),
              login_required=bool(data.get("login_required")), max_files=max_files)
    row = db.execute("SELECT * FROM upload_links WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(success=True, link=link_json(db, row))


@bp.route("/api/upload-links")
@login_required
def list_links():
    db = get_db()
    folder = request.args.get("folder_id")
    if folder is not None:
        f = owned_folder(db, current_user.id, folder)
        rows = db.execute("SELECT * FROM upload_links WHERE user_id = ? AND folder_id IS ? ORDER BY created_at DESC",
                          (current_user.id, f["id"] if f else None)).fetchall()
    else:
        rows = db.execute("SELECT * FROM upload_links WHERE user_id = ? ORDER BY created_at DESC",
                          (current_user.id,)).fetchall()
    return jsonify(links=[link_json(db, r) for r in rows])


@bp.route("/api/upload-links/<int:link_id>", methods=["DELETE"])
@login_required
def revoke_link(link_id):
    db = get_db()
    cur = db.execute("DELETE FROM upload_links WHERE id = ? AND user_id = ?", (link_id, current_user.id))
    db.commit()
    if not cur.rowcount:
        raise ApiError("Link not found.", 404)
    log_event("upload_link_revoke", user=current_user.username, link_id=link_id)
    return jsonify(success=True)


# ---------------------------------------------------------------- public side

def _public_link(token):
    """(row, state): ok | expired | full | missing. A disabled owner's links are gone."""
    row = get_db().execute("SELECT l.*, u.username AS owner FROM upload_links l JOIN users u ON u.id = l.user_id "
                           "WHERE l.token = ? AND u.disabled = 0", (token,)).fetchone()
    return (row, _state(row)) if row else (None, "missing")


def _needs_login(row):
    return row["login_required"] and not current_user.is_authenticated


def _unlocked(row):
    return not row["password_hash"] or row["id"] in session.get("links_unlocked", [])


def _usable_link(token):
    """The link for an upload API call, or an ApiError saying why not."""
    row, state = _public_link(token)
    if state == "missing":
        raise ApiError("This link is invalid or has been revoked.", 404)
    if state == "expired":
        raise ApiError("This upload link has expired.", 410)
    if _needs_login(row):
        raise ApiError("Please log in.", 401)
    if not _unlocked(row):
        raise ApiError("This link is protected by a password.", 403)
    return row


def _link_upload(db, link, upload_id):
    row = db.execute("SELECT * FROM uploads WHERE id = ? AND link_id = ?", (upload_id, link["id"])).fetchone()
    if row is None:
        raise ApiError("Upload not found.", 404)
    return row


@bp.after_request
def _no_index(resp):
    if request.endpoint and request.endpoint.startswith("upload_links.public"):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/r/<token>", methods=["GET"])
def public_page(token):
    row, state = _public_link(token)
    status = {"ok": 200, "missing": 404}.get(state, 410)
    return render_template("upload_link_public.html", link=row if state == "ok" else None, state=state,
                           needs_login=bool(row) and _needs_login(row), unlocked=bool(row) and _unlocked(row),
                           folder=folder_path(get_db(), row["folder_id"]) if row else "",
                           left=None if not row or row["max_files"] is None else row["max_files"] - row["file_count"]), status


@bp.route("/r/<token>", methods=["POST"])
@limiter.limit("10 per minute")
def public_unlock(token):
    row, state = _public_link(token)
    if state != "ok":
        return jsonify(success=False, error="This link is invalid or has expired."), 404 if state == "missing" else 410
    if _needs_login(row):
        return jsonify(success=False, error="Please log in."), 401
    password = str((request.get_json(silent=True) or {}).get("password") or "")
    if row["password_hash"] and not check_password_hash(row["password_hash"], password):
        log_event("upload_link_password_failed", link_id=row["id"])
        return jsonify(success=False, error="Wrong password."), 403
    session["links_unlocked"] = (session.get("links_unlocked", []) + [row["id"]])[-20:]
    return jsonify(success=True)


@bp.route("/r/<token>/uploads", methods=["POST"])
@limiter.limit("60 per minute")
def public_start(token):
    db = get_db()
    link = _usable_link(token)

    def file_limit(db):  # inside the write lock: finished files plus uploads in progress
        if link["max_files"] is None:
            return
        done = db.execute("SELECT file_count FROM upload_links WHERE id = ?", (link["id"],)).fetchone()[0]
        running = db.execute("SELECT COUNT(*) FROM uploads WHERE link_id = ?", (link["id"],)).fetchone()[0]
        if done + running >= link["max_files"]:
            raise ApiError("This upload link has reached its file limit.", 410)

    folder_id = link["folder_id"]
    if folder_id and not db.execute("SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone():
        folder_id = None
    uploader = current_user.username if current_user.is_authenticated else None
    return jsonify(start_upload(db, link["user_id"], request.get_json(silent=True) or {}, folder_id,
                                link=link, uploader=uploader, check=file_limit))


@bp.route("/r/<token>/uploads/<upload_id>", methods=["GET"])
def public_status(token, upload_id):
    db = get_db()
    row = _link_upload(db, _usable_link(token), upload_id)
    return jsonify(success=True, received=row["received"], size=row["total_size"], name=row["name"])


@bp.route("/r/<token>/uploads/<upload_id>", methods=["PUT"])
def public_chunk(token, upload_id):
    db = get_db()
    link = _usable_link(token)
    _link_upload(db, link, upload_id)  # before reading the body
    return put_chunk_for(db, upload_id, lambda: _link_upload(db, link, upload_id))


@bp.route("/r/<token>/uploads/<upload_id>", methods=["DELETE"])
def public_cancel(token, upload_id):
    db = get_db()
    row = _link_upload(db, _usable_link(token), upload_id)
    db.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
    db.commit()
    storage.remove_quietly(storage.abs_path(row["stored_name"]))
    return jsonify(success=True)
