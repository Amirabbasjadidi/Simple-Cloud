import logging
import time

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from . import limiter
from .captcha import consume_captcha, new_captcha
from .db import get_db
from .events import log_event

bp = Blueprint("auth", __name__)
login_manager = LoginManager()
login_manager.login_view = "auth.login"

# Compared against when the username does not exist, so timing doesn't reveal valid usernames.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.username = row["username"]
        self.must_change_password = bool(row["must_change_password"])


@login_manager.user_loader
def load_user(user_id):
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return User(row) if row else None


def _lockout_remaining(ip, username):
    """Seconds until the IP or username may try again (0 = not locked). Shared by all workers."""
    cfg = current_app.config
    window = cfg["LOGIN_LOCKOUT_SECONDS"]
    since = time.time() - window
    db = get_db()
    remaining = 0
    for column, value, limit in (("ip", ip, cfg["LOGIN_MAX_FAILURES_IP"]),
                                 ("username", username, cfg["LOGIN_MAX_FAILURES_USER"])):
        if not value:
            continue
        rows = db.execute(
            f"SELECT ts FROM login_failures WHERE {column} = ? AND ts > ? ORDER BY ts DESC LIMIT ?",
            (value, since, limit),
        ).fetchall()
        if len(rows) >= limit:
            # Locked until the oldest of the last `limit` failures leaves the window.
            remaining = max(remaining, int(rows[-1]["ts"] + window - time.time()) + 1)
    return remaining


def _record_failure(ip, username):
    db = get_db()
    now = time.time()
    db.execute("INSERT INTO login_failures (ip, username, ts) VALUES (?, ?, ?)", (ip, username or None, now))
    db.execute("DELETE FROM login_failures WHERE ts < ?", (now - current_app.config["LOGIN_LOCKOUT_SECONDS"],))
    db.commit()


def _fail(message, status, **extra):
    return jsonify(success=False, error=message, captcha_image=new_captcha(), **extra), status


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("30 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("files.upload_page"))
    if request.method == "GET":
        return render_template("login.html", captcha_image=new_captcha())

    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    ip = request.remote_addr or "unknown"

    wait = _lockout_remaining(ip, username)
    if wait:
        consume_captcha(None)  # still burn the captcha
        log_event("login_locked", logging.WARNING, user=username, retry_after=wait)
        resp, status = _fail(f"Too many failed attempts. Try again in {wait} seconds.", 429)
        resp.headers["Retry-After"] = str(wait)
        return resp, status

    if not consume_captcha(data.get("captcha")):
        _record_failure(ip, username)
        log_event("login_failed", logging.WARNING, user=username, reason="captcha")
        return _fail("Invalid captcha.", 400)

    row = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not check_password_hash(row["password"] if row else _DUMMY_HASH, password) or row is None:
        _record_failure(ip, username)
        log_event("login_failed", logging.WARNING, user=username, reason="credentials")
        return _fail("Invalid username or password.", 401)

    db = get_db()
    db.execute("DELETE FROM login_failures WHERE username = ?", (username,))
    db.commit()
    login_user(User(row), remember=bool(data.get("remember_me")))
    log_event("login_ok", user=username)
    return jsonify(success=True, redirect_url=url_for("files.upload_page"))


@bp.route("/refresh_captcha")
@limiter.limit("60 per minute")
def refresh_captcha():
    return jsonify(captcha_image=new_captcha())


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    log_event("logout", user=current_user.username)
    logout_user()
    return jsonify(success=True, redirect_url=url_for("auth.login"))
