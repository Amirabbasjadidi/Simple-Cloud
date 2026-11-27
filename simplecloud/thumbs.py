"""Thumbnails for PDF, video and music, stored next to the file as <stored_name>.thumb.

- PDF and video: the browser that first lists the file draws page 1 / a frame and uploads a small
  JPEG (PUT). The server has no PDF or video tools; the browser already has pdf.js and a video decoder.
- Music: the cover embedded in the file (ID3v2 APIC, as in most MP3s) is read here on first request.
An empty .thumb file means "tried, nothing to show", so nobody tries again.
"""
import os

from flask import Blueprint, abort, jsonify, request, send_file
from flask_login import login_required

from . import storage
from .files import _owned_file_or_404, preview_kind, thumb_path

bp = Blueprint("thumbs", __name__)

MAX_THUMB = 300 * 1024          # uploaded JPEG
MAX_COVER = 5 * 1024 * 1024     # embedded cover art
MAX_ID3 = 16 * 1024 * 1024      # tag bytes read while looking for the cover
IMAGE_MAGIC = [(b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"),
               (b"GIF8", "image/gif"), (b"RIFF", "image/webp")]


def image_type(data):
    for magic, mime in IMAGE_MAGIC:
        if data.startswith(magic) and (mime != "image/webp" or data[8:12] == b"WEBP"):
            return mime
    return None


def _syncsafe(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def id3_cover(path):
    """Embedded cover image bytes of an ID3v2.3/2.4 tag, or None."""
    with open(path, "rb") as f:
        head = f.read(10)
        if len(head) < 10 or head[:3] != b"ID3" or head[3] not in (3, 4):
            return None
        if head[5] & 0x80:  # whole-tag unsynchronisation: rare, not worth decoding
            return None
        tag = f.read(min(_syncsafe(head[6:10]), MAX_ID3))
    pos = 0
    if head[5] & 0x40:  # extended header
        size = _syncsafe(tag[0:4]) if head[3] == 4 else int.from_bytes(tag[0:4], "big") + 4
        pos = size
    while pos + 10 <= len(tag):
        frame_id = tag[pos:pos + 4]
        if frame_id == b"\0\0\0\0":
            break
        size = _syncsafe(tag[pos + 4:pos + 8]) if head[3] == 4 else int.from_bytes(tag[pos + 4:pos + 8], "big")
        body = tag[pos + 10:pos + 10 + size]
        pos += 10 + size
        if frame_id != b"APIC" or len(body) < 4 or size > MAX_COVER:
            continue
        enc = body[0]
        mime_end = body.find(b"\0", 1)
        if mime_end < 0:
            continue
        i = mime_end + 2  # skip the mime terminator and the picture type byte
        term = b"\0\0" if enc in (1, 2) else b"\0"
        while i < len(body):  # description, terminated in its encoding (UTF-16 ends on an even offset)
            j = body.find(term, i)
            if j < 0:
                break
            if len(term) == 2 and (j - (mime_end + 2)) % 2:
                i = j + 1
                continue
            data = body[j + len(term):]
            return data if image_type(data) else None
        return None
    return None


@bp.route("/files/<int:file_id>/thumb", methods=["GET"])
@login_required
def thumb(file_id):
    row = _owned_file_or_404(file_id)
    path = thumb_path(row["stored_name"])
    if not os.path.exists(path) and preview_kind(row["mime_type"]) == "audio":
        try:
            cover = id3_cover(storage.abs_path(row["stored_name"]))
        except OSError:
            cover = None
        with open(path, "wb") as f:
            f.write(cover or b"")
    try:
        with open(path, "rb") as f:
            mime = image_type(f.read(12))
    except OSError:
        abort(404)
    if not mime:
        abort(404)
    resp = send_file(path, mimetype=mime, conditional=True, max_age=86400)
    resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp


@bp.route("/files/<int:file_id>/thumb", methods=["PUT"])
@login_required
def put_thumb(file_id):
    """A JPEG drawn by the browser (PDF page 1, a video frame); an empty body = no thumbnail."""
    row = _owned_file_or_404(file_id)
    if preview_kind(row["mime_type"]) not in ("pdf", "video"):
        return jsonify(success=False, error="This file type has no generated thumbnail."), 400
    if (request.content_length or 0) > MAX_THUMB:
        return jsonify(success=False, error="Thumbnail too large."), 413
    data = request.get_data(cache=False)
    if data and image_type(data) != "image/jpeg":
        return jsonify(success=False, error="Thumbnail must be a JPEG image."), 400
    path = thumb_path(row["stored_name"])
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return jsonify(success=True, thumb="ready" if data else "none")
