"""Chunked, resumable uploads.

POST   /api/uploads               {name, size, folder_id} -> {upload_id, chunk_size, received}
GET    /api/uploads/<id>          -> {received, size}   (resume: continue from `received`)
PUT    /api/uploads/<id>?offset=N raw bytes; the last chunk completes the upload -> {file}
DELETE /api/uploads/<id>          cancel

Every chunk re-checks ownership, the offset, the declared total size, MAX_FILE_SIZE and the
quota. The declared size is reserved against the quota while the upload is unfinished.
Uploads idle for UPLOAD_ABANDON_SECONDS are removed (on new uploads and at startup).

The same core (start_upload / put_chunk_for / finish) serves upload links (upload_links.py), where
the file goes to the link owner's folder and quota and the upload is identified by its link.
"""
import os
import secrets
import time

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from . import quota, storage
from .db import get_db
from .events import log_event
from .files import ApiError, add_file, file_json, owned_folder
from .i18n import gettext as _

bp = Blueprint("uploads", __name__)


@bp.errorhandler(ApiError)
def _api_error(e):
    return jsonify(success=False, error=str(e)), e.status


def cleanup_abandoned(db, cfg):
    cutoff = time.time() - cfg["UPLOAD_ABANDON_SECONDS"]
    rows = db.execute("SELECT id, stored_name FROM uploads WHERE updated_at < ?", (cutoff,)).fetchall()
    for r in rows:
        db.execute("DELETE FROM uploads WHERE id = ?", (r["id"],))
    db.commit()
    for r in rows:
        storage.remove_quietly(storage.abs_path(r["stored_name"], cfg["UPLOAD_FOLDER"]))
    return len(rows)


def _owned_upload(db, upload_id):
    row = db.execute("SELECT * FROM uploads WHERE id = ? AND user_id = ? AND link_id IS NULL",
                     (upload_id, current_user.id)).fetchone()
    if row is None:
        raise ApiError("Upload not found.", 404)
    return row


@bp.route("/api/uploads", methods=["POST"])
@login_required
def start():
    db = get_db()
    data = request.get_json(silent=True) or {}
    folder = owned_folder(db, current_user.id, data.get("folder_id"))
    return jsonify(start_upload(db, current_user.id, data, folder["id"] if folder else None))


def start_upload(db, owner_id, data, folder_id, link=None, uploader=None, check=None):
    """Create an upload for owner_id (quota reserved). `check(db)` runs inside the write lock
    (upload links: file-count limit). Returns the JSON result; a 0-byte file completes at once."""
    cfg = current_app.config
    cleanup_abandoned(db, cfg)
    try:
        size = int(data.get("size"))
    except (TypeError, ValueError):
        raise ApiError("Invalid file size.")
    if size < 0:
        raise ApiError("Invalid file size.")
    if size > cfg["MAX_FILE_SIZE"]:
        raise ApiError(_("File is too large (max %(n)s MB).", n=cfg['MAX_FILE_SIZE'] // 1024 // 1024), 413)
    name = storage.clean_name(data.get("name"))

    upload_id = secrets.token_urlsafe(18)
    stored = storage.new_stored_name(owner_id) + ".part"
    path = storage.prepare_path(stored)
    open(path, "wb").close()
    now = time.time()
    try:
        db.execute("BEGIN IMMEDIATE")  # quota check + reservation are atomic
        if check:
            check(db)
        quota.check(db, owner_id, size)
        db.execute("INSERT INTO uploads (id, user_id, folder_id, name, total_size, received, stored_name, "
                   "created_at, updated_at, link_id, uploader) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
                   (upload_id, owner_id, folder_id, name, size, stored, now, now,
                    link["id"] if link else None, uploader))
        db.commit()
    except BaseException:
        if db.in_transaction:
            db.rollback()
        storage.remove_quietly(path)
        raise
    result = {"success": True, "upload_id": upload_id, "chunk_size": cfg["CHUNK_SIZE"], "received": 0}
    if size == 0:
        result["file"] = finish(db, db.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone())
    return result


@bp.route("/api/uploads/<upload_id>", methods=["GET"])
@login_required
def status(upload_id):
    row = _owned_upload(get_db(), upload_id)
    return jsonify(success=True, received=row["received"], size=row["total_size"], name=row["name"])


@bp.route("/api/uploads/<upload_id>", methods=["PUT"])
@login_required
def put_chunk(upload_id):
    db = get_db()
    _owned_upload(db, upload_id)  # ownership before reading the body
    return put_chunk_for(db, upload_id, lambda: _owned_upload(db, upload_id))


def put_chunk_for(db, upload_id, reload_row):
    """Write one chunk of an upload the caller has already checked (`reload_row` re-reads it)."""
    cfg = current_app.config
    row = reload_row()
    try:
        offset = int(request.args.get("offset", ""))
    except ValueError:
        raise ApiError("Missing or invalid offset.")
    length = request.content_length
    if length is None:
        raise ApiError("Content-Length is required.", 411)
    if length > cfg["CHUNK_SIZE"] * 2:
        raise ApiError("Chunk too large.", 413)
    if offset < 0 or offset + length > row["total_size"]:
        raise ApiError("Chunk is outside the declared file size.")
    # Read the body before taking the DB write lock, so a slow client can't hold the lock.
    body = request.get_data(cache=False)
    if len(body) != length:
        raise ApiError("Incomplete chunk.")

    path = storage.abs_path(row["stored_name"])
    try:
        db.execute("BEGIN IMMEDIATE")
        row = reload_row()  # re-read under the lock
        if offset != row["received"]:
            db.rollback()
            return jsonify(success=False, error="Unexpected offset.", received=row["received"]), 409
        quota.check(db, row["user_id"], row["total_size"], exclude_upload=upload_id)  # quota may have changed
        with open(path, "r+b") as f:
            f.seek(offset)
            f.write(body)
            f.truncate()
        db.execute("UPDATE uploads SET received = ?, updated_at = ? WHERE id = ?",
                   (offset + length, time.time(), upload_id))
        db.commit()
    except BaseException:
        if db.in_transaction:
            db.rollback()
        raise
    received = offset + length
    result = {"success": True, "received": received}
    if received == row["total_size"]:
        result["file"] = finish(db, reload_row())
    return jsonify(result)


def finish(db, row):
    part = storage.abs_path(row["stored_name"])
    try:
        file_id, name = add_file(db, row["user_id"], row["name"], lambda path: os.replace(part, path),
                                 folder_id=row["folder_id"], exclude_upload=row["id"],
                                 link_id=row["link_id"], uploaded_by=row["uploader"])
        if row["link_id"]:
            db.execute("UPDATE upload_links SET file_count = file_count + 1 WHERE id = ?", (row["link_id"],))
    finally:
        db.execute("DELETE FROM uploads WHERE id = ?", (row["id"],))
        db.commit()
        storage.remove_quietly(part)
    if row["link_id"]:
        log_event("link_upload", user=row["uploader"], link_id=row["link_id"], file_id=file_id, name=name,
                  size=row["total_size"])
    else:
        log_event("upload", user=current_user.username, file_id=file_id, name=name, size=row["total_size"],
                  chunked=True)
    return file_json(db.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone())


@bp.route("/api/uploads/<upload_id>", methods=["DELETE"])
@login_required
def cancel(upload_id):
    db = get_db()
    row = _owned_upload(db, upload_id)
    db.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
    db.commit()
    storage.remove_quietly(storage.abs_path(row["stored_name"]))
    return jsonify(success=True)
