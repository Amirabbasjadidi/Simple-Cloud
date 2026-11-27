import os
import time

from flask import Blueprint, abort, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from . import quota, storage
from .db import get_db
from .events import log_event

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
    row = get_db().execute("SELECT * FROM files WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
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


def folder_path(db, folder_id):
    """'A / B / C' for a folder id (None -> '')."""
    folder = db.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone() if folder_id else None
    return " / ".join(c["name"] for c in _breadcrumbs(db, folder))


def subfolder_ids(db, folder_id):
    """folder_id and every folder below it."""
    return [r[0] for r in db.execute(
        "WITH RECURSIVE sub(id) AS (SELECT id FROM folders WHERE id = ? "
        "UNION ALL SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id) SELECT id FROM sub", (folder_id,))]


def thumb_path(stored_name):
    return storage.abs_path(stored_name + ".thumb")


def destroy_files(db, rows):
    """Permanently delete file rows (after trash). Disk files go only after the commit, so a
    crash leaves unreferenced files, never rows without files."""
    if not rows:
        return
    db.execute(f"DELETE FROM files WHERE id IN ({','.join('?' * len(rows))})", [r["id"] for r in rows])
    db.commit()
    for r in rows:
        storage.remove_quietly(storage.abs_path(r["stored_name"]))
        storage.remove_quietly(thumb_path(r["stored_name"]))


def trash_files(db, user_id, rows, now=None):
    """Move live file rows to the trash (caller commits). They keep counting toward the quota."""
    now = now or time.time()
    for r in rows:
        db.execute("UPDATE files SET deleted_at = ?, trash_folder_id = ?, trash_path = ?, folder_id = NULL "
                   "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
                   (now, r["folder_id"], folder_path(db, r["folder_id"]), r["id"], user_id))


def trash_folder(db, user_id, folder_id):
    """Trash every live file below a folder and delete the folders. Returns (files, folders)."""
    ids = subfolder_ids(db, folder_id)
    marks = ",".join("?" * len(ids))
    rows = db.execute(f"SELECT * FROM files WHERE user_id = ? AND deleted_at IS NULL AND folder_id IN ({marks})",
                      (user_id, *ids)).fetchall()
    trash_files(db, user_id, rows)
    # Deepest folders first is not needed: one statement deletes them all.
    db.execute(f"DELETE FROM folders WHERE id IN ({marks})", ids)
    return len(rows), len(ids)


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


# Non-text/* types that are plain text underneath; previewed as text/plain.
TEXT_LIKE = {"application/json", "application/xml", "application/javascript", "application/x-sh",
             "application/x-yaml", "application/toml", "application/sql", "application/x-python",
             "image/svg+xml"}


def preview_kind(mime):
    """image | video | audio | pdf | text | None. Text is always served as text/plain (never rendered)."""
    if mime in SAFE_INLINE_TYPES and mime.split("/")[0] in ("image", "video", "audio"):
        return mime.split("/")[0]
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("text/") or mime in TEXT_LIKE:
        return "text"
    return None


def _send(row, inline):
    path = storage.abs_path(row["stored_name"])
    if not os.path.isfile(path):
        abort(404)
    kind = preview_kind(row["mime_type"]) if inline else None
    raw = kind == "pdf" and request.args.get("raw") == "1"
    if kind == "text" or raw:
        # raw: the bytes for the in-page PDF viewer (pdf.js). As text/plain with no filename,
        # download managers such as IDM let the request through instead of taking it over.
        mime = "text/plain"  # HTML/SVG/JS source shown as text, never executed (Werkzeug adds charset)
    elif kind:
        mime = row["mime_type"]
    else:
        mime = "application/octet-stream"
    # download_name with non-ASCII characters is sent as RFC 5987 filename*=UTF-8''...
    resp = send_file(path, mimetype=mime, as_attachment=not kind, download_name=row["name"], conditional=True)
    if raw:
        del resp.headers["Content-Disposition"]
    if kind == "pdf":
        # Chrome's PDF viewer is blocked ("This content is blocked") by sandbox or default-src/object-src 'none'.
        csp = "frame-ancestors 'self'"
    else:
        csp = ("default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; "
               "frame-ancestors 'self'; sandbox")
    resp.headers["Content-Security-Policy"] = csp
    return resp


def thumb_state(r):
    """ready | none (tried, nothing to show) | missing (not made yet) | None (kind has no thumbnail)."""
    if preview_kind(r["mime_type"]) not in ("pdf", "video", "audio"):
        return None
    try:
        return "ready" if os.path.getsize(thumb_path(r["stored_name"])) else "none"
    except OSError:
        return "missing"


def file_json(r):
    return {
        "id": r["id"], "name": r["name"], "size": r["size"], "mime_type": r["mime_type"],
        "created_at": r["created_at"], "folder_id": r["folder_id"], "preview_kind": preview_kind(r["mime_type"]),
        "download_url": url_for("files.download", file_id=r["id"]),
        "preview_url": url_for("files.preview", file_id=r["id"]),
        "thumb": thumb_state(r), "thumb_url": url_for("thumbs.thumb", file_id=r["id"]),
        "uploaded_by": r["uploaded_by"], "via_link": r["upload_link_id"] is not None,
    }


def folder_json(r):
    return {"id": r["id"], "name": r["name"], "parent_id": r["parent_id"], "created_at": r["created_at"]}


def add_file(db, user_id, name, write, folder_id=None, exclude_upload=None, link_id=None, uploaded_by=None):
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
            "INSERT INTO files (user_id, folder_id, name, stored_name, size, mime_type, upload_link_id, uploaded_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, folder_id, name, stored, size, storage.guess_mime(name), link_id, uploaded_by))
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
        files = db.execute(f"SELECT * FROM files WHERE user_id = ? AND deleted_at IS NULL AND name LIKE ? ESCAPE '\\' "
                           f"ORDER BY {column} {direction}, id {direction}", (uid, like)).fetchall()
        folder, crumbs = None, []
    else:
        folder = owned_folder(db, uid, request.args.get("folder"))
        fid = folder["id"] if folder else None
        folders = db.execute(f"SELECT * FROM folders WHERE user_id = ? AND parent_id IS ? ORDER BY {folder_order}",
                             (uid, fid)).fetchall()
        files = db.execute(f"SELECT * FROM files WHERE user_id = ? AND folder_id IS ? AND deleted_at IS NULL "
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
    """Move the folder's files to the trash and delete the folder with its subfolders."""
    db = get_db()
    folder = owned_folder(db, current_user.id, folder_id)
    files, folders = trash_folder(db, current_user.id, folder_id)
    db.commit()
    log_event("folder_delete", user=current_user.username, folder_id=folder_id, name=folder["name"],
              folders=folders, files=files)
    return jsonify(success=True, deleted_files=files, deleted_folders=folders)


@bp.route("/api/items/delete", methods=["POST"])
@login_required
def delete_items():
    """Several files and folders to the trash: {"files": [ids], "folders": [ids]}."""
    db = get_db()
    data = request.get_json(silent=True) or {}
    file_ids, folder_ids = _id_list(data.get("files")), _id_list(data.get("folders"))
    rows = [_owned_file_or_404(i) for i in file_ids]
    for i in folder_ids:
        owned_folder(db, current_user.id, i)
    trash_files(db, current_user.id, rows)
    n_files = len(rows)
    for i in folder_ids:
        if db.execute("SELECT 1 FROM folders WHERE id = ?", (i,)).fetchone():  # not inside one deleted above
            n_files += trash_folder(db, current_user.id, i)[0]
    db.commit()
    log_event("items_delete", user=current_user.username, files=n_files, folders=len(folder_ids))
    return jsonify(success=True, deleted_files=n_files)


def _id_list(value, limit=1000):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise ApiError("Invalid selection.")
    try:
        return list(dict.fromkeys(int(v) for v in value))
    except (TypeError, ValueError):
        raise ApiError("Invalid selection.")


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
    if db.execute("SELECT 1 FROM files WHERE user_id = ? AND folder_id IS ? AND name = ? AND id != ? "
                  "AND deleted_at IS NULL", (current_user.id, folder_id, name, file_id)).fetchone():
        raise ApiError("A file with this name already exists there.", 409)
    db.execute("UPDATE files SET name = ?, folder_id = ?, mime_type = ? WHERE id = ?",
               (name, folder_id, storage.guess_mime(name), file_id))
    db.commit()
    log_event("file_update", user=current_user.username, file_id=file_id, name=name, folder_id=folder_id)
    return jsonify(success=True, file=file_json(db.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()))


@bp.route("/api/files/<int:file_id>", methods=["DELETE"])
@login_required
def delete_file(file_id):
    db = get_db()
    row = db.execute("SELECT * FROM files WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
                     (file_id, current_user.id)).fetchone()
    if row is None:
        return jsonify(success=False, error="File not found."), 404
    trash_files(db, current_user.id, [row])
    db.commit()
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
