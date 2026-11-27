"""ZIP downloads of several files and folders, streamed while it is written (no temp file, no
memory for the whole archive). Stored, not compressed: most large files are already compressed,
and it keeps the server fast.

GET /files/zip?files=1,2&folders=3   (owner; files/folders may be combined)
"""
import os
import time
import zipfile
from urllib.parse import quote

from flask import Blueprint, Response, abort, jsonify, request
from flask_login import current_user, login_required

from . import storage
from .db import get_db
from .events import log_event
from .files import ApiError, owned_folder, subfolder_ids

bp = Blueprint("zips", __name__)

CHUNK = 1024 * 1024


@bp.errorhandler(ApiError)
def _api_error(e):
    return jsonify(success=False, error=str(e)), e.status


class _Sink:
    """Write-only file object for zipfile; the generator drains it after each write."""
    def __init__(self):
        self.buf = bytearray()

    def write(self, b):
        self.buf += b
        return len(b)

    def flush(self):
        pass

    def take(self):
        out = bytes(self.buf)
        self.buf.clear()
        return out


def stream_zip(entries):
    """entries: list of (archive name, absolute path). Yields the ZIP bytes."""
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        for arcname, path in entries:
            try:
                f = open(path, "rb")
            except OSError:
                continue  # vanished meanwhile: leave it out rather than break the whole archive
            with f:
                info = zipfile.ZipInfo(arcname, time.localtime(os.fstat(f.fileno()).st_mtime)[:6])
                info.compress_type = zipfile.ZIP_STORED
                with z.open(info, "w", force_zip64=True) as w:
                    while chunk := f.read(CHUNK):
                        w.write(chunk)
                        yield sink.take()
            yield sink.take()
    yield sink.take()


def folder_files(db, folder_id):
    """[(path inside the folder, e.g. "a/b" or "", file row)] for every live file below folder_id."""
    ids = subfolder_ids(db, folder_id)
    marks = ",".join("?" * len(ids))
    names = {r["id"]: (r["name"], r["parent_id"]) for r in
             db.execute(f"SELECT id, name, parent_id FROM folders WHERE id IN ({marks})", ids)}

    def rel(fid):
        parts = []
        while fid != folder_id:
            name, fid = names[fid]
            parts.append(name)
        return "/".join(parts[::-1])

    rows = db.execute(f"SELECT * FROM files WHERE folder_id IN ({marks}) AND deleted_at IS NULL "
                      f"ORDER BY name COLLATE NOCASE", ids).fetchall()
    return sorted(((rel(r["folder_id"]), r) for r in rows), key=lambda x: (x[0] != "", x[0].lower()))


def folder_entries(db, folder_id, prefix):
    """(archive name, path) for every live file below folder_id, under `prefix/`."""
    return [("/".join(p for p in (prefix, rel, r["name"]) if p), storage.abs_path(r["stored_name"]))
            for rel, r in folder_files(db, folder_id)]


def zip_response(entries, name):
    resp = Response(stream_zip(entries), mimetype="application/zip")
    ascii_name = name.encode("ascii", "ignore").decode() or "download.zip"
    resp.headers["Content-Disposition"] = (f'attachment; filename="{ascii_name.replace(chr(34), "")}"; '
                                           f"filename*=UTF-8''{quote(name)}")
    resp.headers["X-Accel-Buffering"] = "no"  # let Nginx pass the stream through as it comes
    return resp


def _ids(arg):
    try:
        return [int(x) for x in request.args.get(arg, "").split(",") if x.strip()][:1000]
    except ValueError:
        abort(400)


@bp.route("/files/zip")
@login_required
def download_zip():
    db = get_db()
    uid = current_user.id
    file_ids, folder_ids = _ids("files"), _ids("folders")
    if not file_ids and not folder_ids:
        abort(400)
    entries = []
    used = set()
    for fid in folder_ids:
        folder = owned_folder(db, uid, fid)
        entries += folder_entries(db, folder["id"], folder["name"])
    for fid in file_ids:
        row = db.execute("SELECT * FROM files WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
                         (fid, uid)).fetchone()
        if row is None:
            abort(404)
        entries.append((row["name"], storage.abs_path(row["stored_name"])))
    # Same name twice (files from different searches/folders): keep both.
    unique = []
    for arc, path in entries:
        base, ext = os.path.splitext(arc)
        candidate, n = arc, 1
        while candidate in used:
            candidate, n = f"{base} ({n}){ext}", n + 1
        used.add(candidate)
        unique.append((candidate, path))
    if len(folder_ids) == 1 and not file_ids:
        name = owned_folder(db, uid, folder_ids[0])["name"] + ".zip"
    else:
        name = "Simple Cloud.zip"
    log_event("zip_download", user=current_user.username, files=len(unique), folders=len(folder_ids))
    return zip_response(unique, name)
