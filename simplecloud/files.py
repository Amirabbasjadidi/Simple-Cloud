import os

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from . import storage
from .db import get_db
from .fetch import FetchError, fetch_to_file

bp = Blueprint("files", __name__)

# Types a browser may render inline from our origin. Everything else (HTML, SVG, XML, JS...) is
# forced to download so uploaded content can't run script on this origin.
SAFE_INLINE_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/avif",
    "video/mp4", "video/webm", "video/ogg",
    "audio/mpeg", "audio/ogg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/aac", "audio/flac",
    "audio/webm", "application/pdf", "text/plain",
}


def _owned_file_or_404(file_id):
    row = get_db().execute("SELECT * FROM files WHERE id = ? AND user_id = ?",
                           (file_id, current_user.id)).fetchone()
    if row is None:
        abort(404)
    return row


def _send(row, inline):
    path = storage.abs_path(row["stored_name"])
    if not os.path.isfile(path):
        abort(404)
    mime = row["mime_type"]
    if mime in ("text/csv", "text/markdown"):
        mime = "text/plain"
    inline = inline and mime in SAFE_INLINE_TYPES
    # download_name with non-ASCII characters is sent as RFC 5987 filename*=UTF-8''...
    resp = send_file(path, mimetype=mime if inline else "application/octet-stream",
                     as_attachment=not inline, download_name=row["name"], conditional=True)
    csp = "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'"
    if mime != "application/pdf":  # Chrome's PDF viewer refuses to run in a sandboxed document
        csp += "; sandbox"
    resp.headers["Content-Security-Policy"] = csp
    return resp


@bp.route("/")
@login_required
def index():
    return redirect(url_for("files.upload_page"))


@bp.route("/upload", methods=["GET"])
@login_required
def upload_page():
    return render_template("Upload.html")


@bp.route("/download", methods=["GET"])
@login_required
def download_page():
    return render_template("Download.html")


@bp.route("/files", methods=["GET"])
@login_required
def files_page():
    return render_template("filemanager.html")


def add_file(user_id, name, write):
    """Store a new file. `write(path)` puts the bytes at path; `name` may be a callable evaluated
    after writing (fetch learns the name from the response). Returns (id, final display name)."""
    db = get_db()
    stored = storage.new_stored_name(user_id)
    path = storage.prepare_path(stored)
    try:
        write(path)
        size = os.path.getsize(path)
        name = storage.unique_name(db, user_id, storage.clean_name(name() if callable(name) else name))
        cur = db.execute(
            "INSERT INTO files (user_id, name, stored_name, size, mime_type) VALUES (?, ?, ?, ?, ?)",
            (user_id, name, stored, size, storage.guess_mime(name)))
        db.commit()
    except BaseException:
        storage.remove_quietly(path)
        raise
    return cur.lastrowid, name


@bp.route("/upload", methods=["POST"])
@login_required
def upload():
    file = request.files.get("file")
    if file is None or file.filename == "":
        return jsonify(success=False, error="No file provided"), 400
    file_id, name = add_file(current_user.id, file.filename, file.save)
    current_app.logger.info("upload user=%r name=%r", current_user.username, name)
    return jsonify(success=True, id=file_id, filename=name)


@bp.route("/download", methods=["POST"])
@login_required
def download_file_from_url():
    url = str((request.get_json(silent=True) or {}).get("url") or "").strip()
    if not url:
        return jsonify(success=False, error="No URL provided"), 400
    hint = {}

    def write(path):
        hint["name"] = fetch_to_file(url, path, current_app.config)[0] or "download"

    try:
        file_id, name = add_file(current_user.id, lambda: hint["name"], write)
    except FetchError as e:
        return jsonify(success=False, error=str(e)), 400
    current_app.logger.info("fetch user=%r name=%r", current_user.username, name)
    return jsonify(success=True, id=file_id, filename=name)


@bp.route("/api/files", methods=["GET"])
@login_required
def api_files():
    rows = get_db().execute("SELECT * FROM files WHERE user_id = ? ORDER BY id DESC", (current_user.id,)).fetchall()
    return jsonify(files=[{
        "id": r["id"], "name": r["name"], "size": r["size"], "mime_type": r["mime_type"],
        "created_at": r["created_at"],
        "download_url": url_for("files.download", file_id=r["id"]),
        "preview_url": url_for("files.preview", file_id=r["id"]),
    } for r in rows])


@bp.route("/api/files/<int:file_id>", methods=["DELETE"])
@login_required
def delete_file(file_id):
    row = get_db().execute("SELECT * FROM files WHERE id = ? AND user_id = ?", (file_id, current_user.id)).fetchone()
    if row is None:
        return jsonify(success=False, error="File not found."), 404
    db = get_db()
    db.execute("DELETE FROM files WHERE id = ?", (file_id,))
    db.commit()
    storage.remove_quietly(storage.abs_path(row["stored_name"]))  # after commit: never a row without its file
    current_app.logger.info("delete user=%r name=%r", current_user.username, row["name"])
    return jsonify(success=True)


@bp.route("/files/<int:file_id>/download")
@login_required
def download(file_id):
    return _send(_owned_file_or_404(file_id), inline=False)


@bp.route("/files/<int:file_id>/preview")
@login_required
def preview(file_id):
    return _send(_owned_file_or_404(file_id), inline=True)
