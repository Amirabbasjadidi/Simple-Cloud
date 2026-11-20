"""Chunked, resumable uploads.

POST   /api/uploads               {name, size, folder_id} -> {upload_id, chunk_size, received}
GET    /api/uploads/<id>          -> {received, size}   (resume: continue from `received`)
PUT    /api/uploads/<id>?offset=N raw bytes; the last chunk completes the upload -> {file}
DELETE /api/uploads/<id>          cancel

Every chunk re-checks ownership, the offset, the declared total size, MAX_FILE_SIZE and the
quota. The declared size is reserved against the quota while the upload is unfinished.
Uploads idle for UPLOAD_ABANDON_SECONDS are removed (on new uploads and at startup).
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
    row = db.execute("SELECT * FROM uploads WHERE id = ? AND user_id = ?", (upload_id, current_user.id)).fetchone()
    if row is None:
        raise ApiError("Upload not found.", 404)
    return row


@bp.route("/api/uploads", methods=["POST"])
@login_required
def start():
    db = get_db()
    cfg = current_app.config
    cleanup_abandoned(db, cfg)
    data = request.get_json(silent=True) or {}
    try:
        size = int(data.get("size"))
    except (TypeError, ValueError):
        raise ApiError("Invalid file size.")
    if size < 0:
        raise ApiError("Invalid file size.")
    if size > cfg["MAX_FILE_SIZE"]:
        raise ApiError(f"File is too large (max {cfg['MAX_FILE_SIZE'] // 1024 // 1024} MB).", 413)
    name = storage.clean_name(data.get("name"))
    folder = owned_folder(db, current_user.id, data.get("folder_id"))

    upload_id = secrets.token_urlsafe(18)
    stored = storage.new_stored_name(current_user.id) + ".part"
    path = storage.prepare_path(stored)
    open(path, "wb").close()
    now = time.time()
    try:
        db.execute("BEGIN IMMEDIATE")  # quota check + reservation are atomic
        quota.check(db, current_user.id, size)
        db.execute("INSERT INTO uploads (id, user_id, folder_id, name, total_size, received, stored_name, "
                   "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
                   (upload_id, current_user.id, folder["id"] if folder else None, name, size, stored, now, now))
        db.commit()
    except BaseException:
        if db.in_transaction:
            db.rollback()
        storage.remove_quietly(path)
        raise
    result = {"success": True, "upload_id": upload_id, "chunk_size": cfg["CHUNK_SIZE"], "received": 0}
    if size == 0:
        result["file"] = _finish(db, _owned_upload(db, upload_id))
    return jsonify(result)


@bp.route("/api/uploads/<upload_id>", methods=["GET"])
@login_required
def status(upload_id):
    row = _owned_upload(get_db(), upload_id)
    return jsonify(success=True, received=row["received"], size=row["total_size"], name=row["name"])


@bp.route("/api/uploads/<upload_id>", methods=["PUT"])
@login_required
def put_chunk(upload_id):
    db = get_db()
    cfg = current_app.config
    row = _owned_upload(db, upload_id)  # ownership before reading the body
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
        row = _owned_upload(db, upload_id)  # re-read under the lock
        if offset != row["received"]:
            db.rollback()
            return jsonify(success=False, error="Unexpected offset.", received=row["received"]), 409
        quota.check(db, current_user.id, row["total_size"], exclude_upload=upload_id)  # quota may have changed
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
        result["file"] = _finish(db, _owned_upload(db, upload_id))
    return jsonify(result)


def _finish(db, row):
    part = storage.abs_path(row["stored_name"])
    try:
        file_id, name = add_file(db, row["user_id"], row["name"], lambda path: os.replace(part, path),
                                 folder_id=row["folder_id"], exclude_upload=row["id"])
    finally:
        db.execute("DELETE FROM uploads WHERE id = ?", (row["id"],))
        db.commit()
        storage.remove_quietly(part)
    log_event("upload", user=current_user.username, file_id=file_id, name=name, size=row["total_size"], chunked=True)
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
