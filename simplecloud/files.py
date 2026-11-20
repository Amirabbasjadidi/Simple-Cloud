import os

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from . import quota, storage
from .db import get_db
from .events import log_event
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

SORT_COLUMNS = {"name": "name COLLATE NOCASE", "size": "size", "date": "created_at"}


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@bp.errorhandler(ApiError)
def _api_error(e):
    return jsonify(success=False, error=str(e)), e.status


# ---------------------------------------------------------------- helpers

def _owned_file_or_404(file_id):
    row = get_db().execute("SELECT * FROM files WHERE id = ? AND user_id = ?",
                           (file_id, current_user.id)).fetchone()
    if row is None:
        abort(404)
    return row


def owned_folder(db, user_id, folder_id):
    """Folder row for folder_id (None/'root'/'' -> None = root). Not owned -> 404."""
    if folder_id in (None, "", "root", "null"):
        return None
    try:
        folder_id = int(folder_id)
    except (TypeError, ValueError):
        raise ApiError("Folder not found.", 404)
    row = db.execute("SELECT * FROM folders WHERE id = ? AND user_id = ?", (folder_id, user_id)).fetchone()
    if row is None:
        raise ApiError("Folder not found.", 404)
    return row


def _breadcrumbs(db, folder):
    crumbs = []
    while folder is not None:
        crumbs.append({"id": folder["id"], "name": folder["name"]})
        folder = db.execute("SELECT * FROM folders WHERE id = ?", (folder["parent_id"],)).fetchone() \
            if folder["parent_id"] else None
    return crumbs[::-1]


def _folder_name(db, user_id, parent_id, name, exclude_id=None):
    name = storage.clean_name(name, default="")
    if not name:
        raise ApiError("Folder name must not be empty.")
    if db.execute("SELECT 1 FROM folders WHERE user_id = ? AND parent_id IS ? AND name = ? AND id IS NOT ?",
                  (user_id, parent_id, name, exclude_id)).fetchone():
        raise ApiError("A folder with this name already exists here.", 409)
    return name


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


def file_json(r):
    return {
        "id": r["id"], "name": r["name"], "size": r["size"], "mime_type": r["mime_type"],
        "created_at": r["created_at"], "folder_id": r["folder_id"],
        "download_url": url_for("files.download", file_id=r["id"]),
        "preview_url": url_for("files.preview", file_id=r["id"]),
    }


def folder_json(r):
    return {"id": r["id"], "name": r["name"], "parent_id": r["parent_id"], "created_at": r["created_at"]}


def add_file(db, user_id, name, write, folder_id=None, exclude_upload=None):
    """Store a new file for user_id. Usable outside a request (background jobs pass their own db).

    `write(path)` puts the bytes at path; `name` may be a callable evaluated after writing (fetch
    learns the name from the response). The quota check and the insert share one write
    transaction, so concurrent uploads can't overshoot the quota. If the target folder was
    deleted meanwhile the file goes to the root. Returns (id, display name).
    """
    stored = storage.new_stored_name(user_id)
    path = storage.prepare_path(stored)
    try:
        write(path)
        size = os.path.getsize(path)
        db.execute("BEGIN IMMEDIATE")
        quota.check(db, user_id, size, exclude_upload)
        if folder_id is not None and not db.execute("SELECT 1 FROM folders WHERE id = ? AND user_id = ?",
                                                    (folder_id, user_id)).fetchone():
            folder_id = None
        name = storage.clean_name(name() if callable(name) else name)
        name = storage.unique_name(db, user_id, name, folder_id)
        cur = db.execute(
            "INSERT INTO files (user_id, folder_id, name, stored_name, size, mime_type) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, folder_id, name, stored, size, storage.guess_mime(name)))
        db.commit()
    except BaseException:
        if db.in_transaction:
            db.rollback()
        storage.remove_quietly(path)
        raise
    return cur.lastrowid, name


# ---------------------------------------------------------------- pages

@bp.route("/")
@login_required
def index():
    return redirect(url_for("files.files_page"))


@bp.route("/upload", methods=["GET"])
@login_required
def upload_page():
    return render_template("upload.html")


@bp.route("/download", methods=["GET"])
@login_required
def download_page():
    return render_template("fetch.html")


@bp.route("/files", methods=["GET"])
@login_required
def files_page():
    return render_template("files.html")


# ---------------------------------------------------------------- upload / fetch

@bp.route("/upload", methods=["POST"])
@login_required
def upload():
    # Check before touching request.files, which would read the whole body.
    left = quota.remaining(get_db(), current_user.id)
    if left is not None and (request.content_length or 0) > left + 64 * 1024:  # multipart overhead
        raise quota.QuotaError("Not enough storage space: this would exceed your quota.")
    file = request.files.get("file")
    if file is None or file.filename == "":
        return jsonify(success=False, error="No file provided"), 400
    folder = owned_folder(get_db(), current_user.id, request.form.get("folder_id"))
    file_id, name = add_file(get_db(), current_user.id, file.filename, file.save,
                             folder_id=folder["id"] if folder else None)
    log_event("upload", user=current_user.username, file_id=file_id, name=name)
    return jsonify(success=True, id=file_id, filename=name)


@bp.route("/download", methods=["POST"])
@login_required
def download_file_from_url():
    url = str((request.get_json(silent=True) or {}).get("url") or "").strip()
    if not url:
        return jsonify(success=False, error="No URL provided"), 400
    hint = {}
    cfg = dict(current_app.config)
    left = quota.remaining(get_db(), current_user.id)
    cfg["FETCH_MAX_BYTES"] = min(x for x in (cfg["FETCH_MAX_BYTES"], cfg["MAX_FILE_SIZE"], left) if x is not None)

    def write(path):
        hint["name"] = fetch_to_file(url, path, cfg)[0] or "download"

    try:
        file_id, name = add_file(get_db(), current_user.id, lambda: hint["name"], write)
    except FetchError as e:
        log_event("fetch_failed", user=current_user.username, url=url, error=e)
        return jsonify(success=False, error=str(e)), 400
    log_event("fetch_ok", user=current_user.username, file_id=file_id, name=name, url=url)
    return jsonify(success=True, id=file_id, filename=name)


# ---------------------------------------------------------------- listing / search

@bp.route("/api/usage")
@login_required
def api_usage():
    return jsonify(quota.usage(get_db(), current_user.id))


@bp.route("/api/files", methods=["GET"])
@login_required
def api_files():
    """?folder=<id>|root  ?q=<search in all folders>  ?sort=name|size|date  ?order=asc|desc"""
    db = get_db()
    uid = current_user.id
    sort = request.args.get("sort", "date")
    sort = sort if sort in SORT_COLUMNS else "date"
    column = SORT_COLUMNS[sort]
    direction = "ASC" if request.args.get("order", "desc" if sort == "date" else "asc") == "asc" else "DESC"
    folder_order = f"{'created_at' if sort == 'date' else 'name COLLATE NOCASE'} {direction}"
    q = request.args.get("q", "").strip()

    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        folders = db.execute(f"SELECT * FROM folders WHERE user_id = ? AND name LIKE ? ESCAPE '\\' "
                             f"ORDER BY {folder_order}", (uid, like)).fetchall()
        files = db.execute(f"SELECT * FROM files WHERE user_id = ? AND name LIKE ? ESCAPE '\\' "
                           f"ORDER BY {column} {direction}, id {direction}", (uid, like)).fetchall()
        folder, crumbs = None, []
    else:
        folder = owned_folder(db, uid, request.args.get("folder"))
        fid = folder["id"] if folder else None
        folders = db.execute(f"SELECT * FROM folders WHERE user_id = ? AND parent_id IS ? ORDER BY {folder_order}",
                             (uid, fid)).fetchall()
        files = db.execute(f"SELECT * FROM files WHERE user_id = ? AND folder_id IS ? "
                           f"ORDER BY {column} {direction}, id {direction}", (uid, fid)).fetchall()
        crumbs = _breadcrumbs(db, folder)
    return jsonify(folder=folder_json(folder) if folder else None, breadcrumbs=crumbs, search=bool(q),
                   folders=[folder_json(f) for f in folders], files=[file_json(f) for f in files])


@bp.route("/api/folders", methods=["GET"])
@login_required
def list_folders():
    rows = get_db().execute("SELECT * FROM folders WHERE user_id = ? ORDER BY name COLLATE NOCASE",
                            (current_user.id,)).fetchall()
    return jsonify(folders=[folder_json(r) for r in rows])


# ---------------------------------------------------------------- folders

@bp.route("/api/folders", methods=["POST"])
@login_required
def create_folder():
    db = get_db()
    data = request.get_json(silent=True) or {}
    parent = owned_folder(db, current_user.id, data.get("parent_id"))
    pid = parent["id"] if parent else None
    name = _folder_name(db, current_user.id, pid, data.get("name"))
    cur = db.execute("INSERT INTO folders (user_id, parent_id, name) VALUES (?, ?, ?)", (current_user.id, pid, name))
    db.commit()
    log_event("folder_create", user=current_user.username, folder_id=cur.lastrowid, name=name)
    return jsonify(success=True, folder=folder_json(db.execute("SELECT * FROM folders WHERE id = ?",
                                                                (cur.lastrowid,)).fetchone()))


@bp.route("/api/folders/<int:folder_id>", methods=["PATCH"])
@login_required
def update_folder(folder_id):
    db = get_db()
    folder = owned_folder(db, current_user.id, folder_id)
    data = request.get_json(silent=True) or {}
    parent_id = folder["parent_id"]
    if "parent_id" in data:
        parent = owned_folder(db, current_user.id, data["parent_id"])
        parent_id = parent["id"] if parent else None
        # walk up from the new parent: reaching this folder would create a cycle
        node = parent
        while node is not None:
            if node["id"] == folder_id:
                raise ApiError("A folder cannot be moved into itself.")
            node = db.execute("SELECT * FROM folders WHERE id = ?", (node["parent_id"],)).fetchone() \
                if node["parent_id"] else None
    name = _folder_name(db, current_user.id, parent_id, data.get("name", folder["name"]), exclude_id=folder_id)
    db.execute("UPDATE folders SET name = ?, parent_id = ? WHERE id = ?", (name, parent_id, folder_id))
    db.commit()
    log_event("folder_update", user=current_user.username, folder_id=folder_id, name=name, parent_id=parent_id)
    return jsonify(success=True)


@bp.route("/api/folders/<int:folder_id>", methods=["DELETE"])
@login_required
def delete_folder(folder_id):
    """Delete a folder with everything in it."""
    db = get_db()
    owned_folder(db, current_user.id, folder_id)
    ids = [r[0] for r in db.execute(
        "WITH RECURSIVE sub(id) AS (SELECT id FROM folders WHERE id = ? "
        "UNION ALL SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id) SELECT id FROM sub", (folder_id,))]
    marks = ",".join("?" * len(ids))
    stored = [r[0] for r in db.execute(f"SELECT stored_name FROM files WHERE folder_id IN ({marks})", ids)]
    db.execute(f"DELETE FROM files WHERE folder_id IN ({marks})", ids)
    db.execute(f"DELETE FROM folders WHERE id IN ({marks})", ids)
    db.commit()
    for name in stored:  # after commit: never a row without its file
        storage.remove_quietly(storage.abs_path(name))
    log_event("folder_delete", user=current_user.username, folder_id=folder_id, folders=len(ids), files=len(stored))
    return jsonify(success=True, deleted_files=len(stored), deleted_folders=len(ids))


# ---------------------------------------------------------------- single file

@bp.route("/api/files/<int:file_id>", methods=["PATCH"])
@login_required
def update_file(file_id):
    """Rename and/or move: {"name": "...", "folder_id": <id>|null}."""
    db = get_db()
    row = _owned_file_or_404(file_id)
    data = request.get_json(silent=True) or {}
    folder_id = row["folder_id"]
    if "folder_id" in data:
        folder = owned_folder(db, current_user.id, data["folder_id"])
        folder_id = folder["id"] if folder else None
    name = storage.clean_name(data.get("name", row["name"]), default="")
    if not name:
        raise ApiError("File name must not be empty.")
    if db.execute("SELECT 1 FROM files WHERE user_id = ? AND folder_id IS ? AND name = ? AND id != ?",
                  (current_user.id, folder_id, name, file_id)).fetchone():
        raise ApiError("A file with this name already exists there.", 409)
    db.execute("UPDATE files SET name = ?, folder_id = ?, mime_type = ? WHERE id = ?",
               (name, folder_id, storage.guess_mime(name), file_id))
    db.commit()
    log_event("file_update", user=current_user.username, file_id=file_id, name=name, folder_id=folder_id)
    return jsonify(success=True, file=file_json(db.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()))


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
    log_event("delete", user=current_user.username, file_id=file_id, name=row["name"])
    return jsonify(success=True)


@bp.route("/files/<int:file_id>/download")
@login_required
def download(file_id):
    return _send(_owned_file_or_404(file_id), inline=False)


@bp.route("/files/<int:file_id>/preview")
@login_required
def preview(file_id):
    return _send(_owned_file_or_404(file_id), inline=True)
