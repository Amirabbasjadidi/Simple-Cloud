"""Share links: random token, optional expiry, optional password, optional download limit, revocable."""
import secrets
import time

from flask import Blueprint, jsonify, render_template, request, session, url_for
from flask_login import current_user, login_required
from werkzeug.security import check_password_hash, generate_password_hash

from . import limiter
from .db import get_db
from .events import log_event
from .files import ApiError, _owned_file_or_404, _send

bp = Blueprint("shares", __name__)

MAX_EXPIRY_HOURS = 24 * 365


@bp.errorhandler(ApiError)
def _api_error(e):
    return jsonify(success=False, error=str(e)), e.status


def share_json(r):
    return {
        "id": r["id"], "file_id": r["file_id"], "file_name": r["file_name"],
        "url": url_for("shares.public_page", token=r["token"], _external=True),
        "created_at": r["created_at"], "expires_at": r["expires_at"], "has_password": bool(r["password_hash"]),
        "max_downloads": r["max_downloads"], "download_count": r["download_count"],
        "expired": _state(r) != "ok",
    }


def _state(r):
    if r["expires_at"] is not None and r["expires_at"] <= time.time():
        return "expired"
    if r["max_downloads"] is not None and r["download_count"] >= r["max_downloads"]:
        return "exhausted"
    return "ok"


def _positive_int(value, what, maximum):
    if value in (None, ""):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ApiError(f"Invalid {what}.")
    if not 0 < value <= maximum:
        raise ApiError(f"Invalid {what}.")
    return value


# ---------------------------------------------------------------- owner side

@bp.route("/shares")
@login_required
def shares_page():
    return render_template("shares.html")


@bp.route("/api/files/<int:file_id>/shares", methods=["POST"])
@login_required
def create_share(file_id):
    _owned_file_or_404(file_id)
    data = request.get_json(silent=True) or {}
    hours = _positive_int(data.get("expires_hours"), "expiry", MAX_EXPIRY_HOURS)
    max_dl = _positive_int(data.get("max_downloads"), "download limit", 1_000_000)
    password = str(data.get("password") or "")
    db = get_db()
    now = time.time()
    cur = db.execute(
        "INSERT INTO shares (token, user_id, file_id, created_at, expires_at, password_hash, max_downloads) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (secrets.token_urlsafe(24), current_user.id, file_id, now, now + hours * 3600 if hours else None,
         generate_password_hash(password) if password else None, int(max_dl) if max_dl else None))
    db.commit()
    log_event("share_create", user=current_user.username, file_id=file_id, share_id=cur.lastrowid,
              expires_hours=hours, password=bool(password), max_downloads=max_dl)
    return jsonify(success=True, share=share_json(_shares(db, "s.id = ?", cur.lastrowid)[0]))


def _shares(db, where, *params):
    return db.execute("SELECT s.*, f.name AS file_name FROM shares s JOIN files f ON f.id = s.file_id "
                      f"WHERE s.user_id = ? AND {where} ORDER BY s.created_at DESC",
                      (current_user.id, *params)).fetchall()


@bp.route("/api/shares")
@login_required
def list_shares():
    file_id = request.args.get("file_id", type=int)
    rows = _shares(get_db(), "s.file_id = ?", file_id) if file_id else _shares(get_db(), "1")
    return jsonify(shares=[share_json(r) for r in rows])


@bp.route("/api/shares/<int:share_id>", methods=["DELETE"])
@login_required
def revoke_share(share_id):
    db = get_db()
    cur = db.execute("DELETE FROM shares WHERE id = ? AND user_id = ?", (share_id, current_user.id))
    db.commit()
    if not cur.rowcount:
        raise ApiError("Share not found.", 404)
    log_event("share_revoke", user=current_user.username, share_id=share_id)
    return jsonify(success=True)


# ---------------------------------------------------------------- public side

def _public_share(token):
    """(row, state) where state is ok | expired | exhausted | missing."""
    row = get_db().execute("SELECT s.*, f.name, f.name AS file_name, f.size, f.mime_type, f.stored_name "
                           "FROM shares s JOIN files f ON f.id = s.file_id JOIN users u ON u.id = s.user_id "
                           "WHERE s.token = ? AND u.disabled = 0", (token,)).fetchone()
    return (row, _state(row)) if row else (None, "missing")


def _unlocked(row):
    return not row["password_hash"] or row["id"] in session.get("shares_unlocked", [])


def _gone(state):
    return render_template("share_public.html", share=None, state=state), 404 if state == "missing" else 410


@bp.after_request
def _no_index(resp):
    if request.endpoint and request.endpoint.startswith("shares.public"):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/s/<token>", methods=["GET"])
def public_page(token):
    row, state = _public_share(token)
    if state != "ok":
        return _gone(state)
    return render_template("share_public.html", share=row, state=state, unlocked=_unlocked(row),
                           remaining=None if row["max_downloads"] is None else row["max_downloads"] - row["download_count"])


@bp.route("/s/<token>", methods=["POST"])
@limiter.limit("10 per minute")
def public_unlock(token):
    row, state = _public_share(token)
    if state != "ok":
        return jsonify(success=False, error="This link is invalid or has expired."), 404 if state == "missing" else 410
    password = str((request.get_json(silent=True) or {}).get("password") or "")
    if row["password_hash"] and not check_password_hash(row["password_hash"], password):
        log_event("share_password_failed", share_id=row["id"])
        return jsonify(success=False, error="Wrong password."), 403
    session["shares_unlocked"] = (session.get("shares_unlocked", []) + [row["id"]])[-20:]
    return jsonify(success=True)


@bp.route("/s/<token>/download")
def public_download(token):
    row, state = _public_share(token)
    if state != "ok":
        return _gone(state)
    if not _unlocked(row):
        return render_template("share_public.html", share=row, state=state, unlocked=False, remaining=None), 403
    # Count a download once: resumed/partial requests (Range not starting at 0) are free.
    rng = request.headers.get("Range", "")
    if not rng or rng.replace(" ", "").startswith("bytes=0-"):
        db = get_db()
        cur = db.execute("UPDATE shares SET download_count = download_count + 1 WHERE id = ? "
                         "AND (max_downloads IS NULL OR download_count < max_downloads)", (row["id"],))
        db.commit()
        if not cur.rowcount:
            return _gone("exhausted")
        log_event("share_download", share_id=row["id"], file_id=row["file_id"])
    return _send(row, inline=False)
