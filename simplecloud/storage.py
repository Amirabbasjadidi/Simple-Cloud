"""On-disk layout: <UPLOAD_FOLDER>/<user_id>/<uuid hex>. The display name lives only in the DB."""
import mimetypes
import os
import unicodedata
import uuid

from flask import current_app

# Zero-width non-joiner/joiner are needed for correct Persian text; other format chars
# (bidi overrides like U+202E, etc.) are stripped so names can't spoof their extension.
_KEEP_FORMAT = {"‌", "‍"}
MAX_NAME = 255


def clean_name(name, default="file"):
    """Sanitize a display name: strip path parts and control chars, keep Unicode (Persian, etc.)."""
    name = unicodedata.normalize("NFC", str(name or ""))
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(
        ch for ch in name
        if unicodedata.category(ch)[0] != "C" or ch in _KEEP_FORMAT
    ).strip()
    if name in ("", ".", ".."):
        return default
    if len(name) > MAX_NAME:
        base, ext = os.path.splitext(name)
        ext = ext[:20]
        name = base[: MAX_NAME - len(ext)] + ext
    return name


def guess_mime(name):
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def new_stored_name(user_id):
    return f"{int(user_id)}/{uuid.uuid4().hex}"


def abs_path(stored_name, upload_folder=None):
    root = os.path.abspath(upload_folder or current_app.config["UPLOAD_FOLDER"])
    path = os.path.abspath(os.path.join(root, stored_name))
    if os.path.commonpath([root, path]) != root:  # defense in depth; stored_name is server-generated
        raise ValueError("invalid stored name")
    return path


def prepare_path(stored_name):
    path = abs_path(stored_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def unique_name(db, user_id, name, folder_id=None):
    """'a.txt' -> 'a (1).txt' if this folder already has a file with that name."""
    base, ext = os.path.splitext(name)
    candidate, n = name, 1
    while db.execute("SELECT 1 FROM files WHERE user_id = ? AND folder_id IS ? AND name = ? AND deleted_at IS NULL",
                     (user_id, folder_id, candidate)).fetchone():
        candidate = f"{base} ({n}){ext}"
        n += 1
    return candidate


def remove_quietly(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
