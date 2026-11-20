import mimetypes
import os

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

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


def get_unique_filename(directory, filename):
    base, ext = os.path.splitext(filename)
    counter = 1
    new_filename = filename
    while os.path.exists(os.path.join(directory, new_filename)):
        new_filename = f"{base} ({counter}){ext}"
        counter += 1
    return new_filename


def _owned_file_or_404(file_id):
    row = get_db().execute("SELECT * FROM files WHERE id = ? AND uploaded_by = ?",
                           (file_id, current_user.username)).fetchone()
    if row is None:
        abort(404)
    return row


def _file_path(row):
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], os.path.basename(row["name"]))
    if not os.path.isfile(path):
        abort(404)
    return path


def _send(row, inline):
    mime = mimetypes.guess_type(row["name"])[0] or "application/octet-stream"
    if mime.startswith("text/") and mime in SAFE_INLINE_TYPES | {"text/csv", "text/markdown"}:
        mime = "text/plain"
    inline = inline and mime in SAFE_INLINE_TYPES
    resp = send_file(_file_path(row), mimetype=mime if inline else "application/octet-stream",
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


def _store(name):
    name = secure_filename(name) or "download"
    name = get_unique_filename(current_app.config["UPLOAD_FOLDER"], name)
    return name, os.path.join(current_app.config["UPLOAD_FOLDER"], name)


def _insert(name, path):
    db = get_db()
    cur = db.execute("INSERT INTO files (name, path, uploaded_by) VALUES (?, ?, ?)",
                     (name, path, current_user.username))
    db.commit()
    return cur.lastrowid


@bp.route("/upload", methods=["POST"])
@login_required
def upload():
    file = request.files.get("file")
    if file is None or file.filename == "":
        return jsonify(success=False, error="No file provided"), 400
    name, path = _store(file.filename)
    file.save(path)
    _insert(name, path)
    current_app.logger.info("upload user=%r name=%r", current_user.username, name)
    return jsonify(success=True, filename=name)


@bp.route("/download", methods=["POST"])
@login_required
def download_file_from_url():
    url = str((request.get_json(silent=True) or {}).get("url") or "").strip()
    if not url:
        return jsonify(success=False, error="No URL provided"), 400
    tmp = os.path.join(current_app.config["UPLOAD_FOLDER"], f".fetch-{os.urandom(8).hex()}.part")
    try:
        hint, _size, _ctype = fetch_to_file(url, tmp, current_app.config)
    except FetchError as e:
        return jsonify(success=False, error=str(e)), 400
    name, path = _store(hint)
    os.replace(tmp, path)
    _insert(name, path)
    current_app.logger.info("fetch user=%r name=%r", current_user.username, name)
    return jsonify(success=True, filename=name)


@bp.route("/api/files", methods=["GET"])
@login_required
def api_files():
    rows = get_db().execute("SELECT id, name FROM files WHERE uploaded_by = ?", (current_user.username,)).fetchall()
    return jsonify(files=[{
        "id": r["id"], "name": r["name"],
        "download_url": url_for("files.download", file_id=r["id"]),
        "preview_url": url_for("files.preview", file_id=r["id"]),
    } for r in rows])


@bp.route("/api/files/<int:file_id>", methods=["DELETE"])
@login_required
def delete_file(file_id):
    row = get_db().execute("SELECT * FROM files WHERE id = ? AND uploaded_by = ?",
                           (file_id, current_user.username)).fetchone()
    if row is None:
        return jsonify(success=False, error="File not found."), 404
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], os.path.basename(row["name"]))
    if os.path.exists(path):
        os.remove(path)
    db = get_db()
    db.execute("DELETE FROM files WHERE id = ?", (file_id,))
    db.commit()
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
