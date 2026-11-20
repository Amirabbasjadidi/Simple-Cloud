import logging
import time

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from . import limiter
from .captcha import consume_captcha, new_captcha
from . import totp, users
from .db import get_db
from .events import log_event
from .i18n import gettext as _

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
        self.is_admin = bool(row["is_admin"])
        self.session_version = row["session_version"]
        self.quota_bytes = row["quota_bytes"]
        self.totp_enabled = bool(row["totp_secret"])

    def get_id(self):
        # Changing the password or disabling the user bumps session_version, which invalidates
        # every session and remember-me cookie issued before.
        return f"{self.id}:{self.session_version}"


@login_manager.user_loader
def load_user(user_id):
    try:
        uid, version = (int(x) for x in str(user_id).split(":"))
    except ValueError:
        return None
    row = get_db().execute("SELECT * FROM users WHERE id = ? AND session_version = ? AND disabled = 0",
                           (uid, version)).fetchone()
    return User(row) if row else None


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith("/api/") or request.method != "GET":
        return jsonify(success=False, error="Please log in."), 401
    return redirect(url_for("auth.login", next=request.path))


@bp.before_app_request
def force_password_change():
    if (current_user.is_authenticated and current_user.must_change_password
            and request.endpoint not in ("auth.account_page", "auth.change_password", "auth.logout", "i18n.set_language", "static")):
        if request.path.startswith("/api/") or request.method != "GET":
            return jsonify(success=False, error="You must change your password first.",
                           must_change_password=True), 403
        return redirect(url_for("auth.account_page"))


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
        return redirect(url_for("files.files_page"))
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
        resp, status = _fail(_("Too many failed attempts. Try again in %(n)s seconds.", n=wait), 429)
        resp.headers["Retry-After"] = str(wait)
        return resp, status

    if not consume_captcha(data.get("captcha")):
        _record_failure(ip, username)
        log_event("login_failed", logging.WARNING, user=username, reason="captcha")
        return _fail("Invalid captcha.", 400)

    row = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not check_password_hash(row["password"] if row else _DUMMY_HASH, password) or row is None or row["disabled"]:
        _record_failure(ip, username)
        log_event("login_failed", logging.WARNING, user=username, reason="credentials")
        return _fail("Invalid username or password.", 401)

    if row["totp_secret"]:
        # Second step. Failures are only cleared after the code, so re-entering the password
        # can't reset the counter for TOTP guessing.
        session["pending_2fa"] = {"uid": row["id"], "sv": row["session_version"], "ts": time.time(),
                                  "remember": bool(data.get("remember_me"))}
        return jsonify(success=True, totp_required=True)
    return _complete_login(row, bool(data.get("remember_me")))


@bp.route("/login/totp", methods=["POST"])
@limiter.limit("30 per minute")
def login_totp():
    pending = session.get("pending_2fa")
    if not pending or time.time() - pending["ts"] > 300:
        session.pop("pending_2fa", None)
        return jsonify(success=False, error="Your sign-in expired. Please start again.", restart=True), 400
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ? AND session_version = ? AND disabled = 0",
                     (pending["uid"], pending["sv"])).fetchone()
    if row is None or not row["totp_secret"]:
        session.pop("pending_2fa", None)
        return jsonify(success=False, error="Your sign-in expired. Please start again.", restart=True), 400
    ip = request.remote_addr or "unknown"
    wait = _lockout_remaining(ip, row["username"])
    if wait:
        session.pop("pending_2fa", None)
        log_event("login_locked", logging.WARNING, user=row["username"], retry_after=wait)
        return jsonify(success=False, error=_("Too many failed attempts. Try again in %(n)s seconds.", n=wait),
                       restart=True), 429
    step = totp.verify(row["totp_secret"], (request.get_json(silent=True) or {}).get("code"), row["totp_last_step"])
    # Atomic claim of the time step: the same code can't be used twice.
    if step is None or not db.execute("UPDATE users SET totp_last_step = ? WHERE id = ? AND totp_last_step < ?",
                                      (step, row["id"], step)).rowcount:
        db.commit()
        _record_failure(ip, row["username"])
        log_event("login_failed", logging.WARNING, user=row["username"], reason="totp")
        return jsonify(success=False, error="Invalid code."), 401
    db.commit()
    session.pop("pending_2fa", None)
    return _complete_login(row, pending["remember"])


def _complete_login(row, remember):
    db = get_db()
    db.execute("DELETE FROM login_failures WHERE username = ?", (row["username"],))
    db.commit()
    login_user(User(row), remember=remember)
    log_event("login_ok", user=row["username"])
    target = url_for("auth.account_page") if row["must_change_password"] else url_for("files.files_page")
    return jsonify(success=True, redirect_url=target)


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


@bp.route("/account")
@login_required
def account_page():
    return render_template("account.html")


@bp.route("/api/account/password", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def change_password():
    data = request.get_json(silent=True) or {}
    current, new = str(data.get("current_password") or ""), str(data.get("new_password") or "")
    db = get_db()
    row = db.execute("SELECT password FROM users WHERE id = ?", (current_user.id,)).fetchone()
    if not check_password_hash(row["password"], current):
        log_event("password_change_failed", logging.WARNING, user=current_user.username)
        return jsonify(success=False, error="Current password is incorrect."), 400
    if new == current:
        return jsonify(success=False, error="The new password must be different."), 400
    try:
        users.set_password(db, current_user.id, new)
    except users.UserError as e:
        return jsonify(success=False, error=str(e)), 400
    # set_password bumped session_version: re-login this session with the new version.
    login_user(User(db.execute("SELECT * FROM users WHERE id = ?", (current_user.id,)).fetchone()))
    log_event("password_changed", user=current_user.username)
    return jsonify(success=True)


@bp.route("/api/account/totp/setup", methods=["POST"])
@login_required
def totp_setup():
    if current_user.totp_enabled:
        return jsonify(success=False, error="Two-factor authentication is already on."), 400
    secret = totp.new_secret()
    db = get_db()
    db.execute("UPDATE users SET totp_pending_secret = ? WHERE id = ?", (secret, current_user.id))
    db.commit()
    return jsonify(success=True, secret=secret, uri=totp.provisioning_uri(secret, current_user.username))


@bp.route("/api/account/totp/enable", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def totp_enable():
    db = get_db()
    row = db.execute("SELECT totp_pending_secret FROM users WHERE id = ?", (current_user.id,)).fetchone()
    if not row["totp_pending_secret"]:
        return jsonify(success=False, error="Start the setup first."), 400
    step = totp.verify(row["totp_pending_secret"], (request.get_json(silent=True) or {}).get("code"))
    if step is None:
        return jsonify(success=False, error="Invalid code. Check the time on your phone and try again."), 400
    db.execute("UPDATE users SET totp_secret = totp_pending_secret, totp_pending_secret = NULL, totp_last_step = ? "
               "WHERE id = ?", (step, current_user.id))
    db.commit()
    log_event("totp_enabled", user=current_user.username)
    return jsonify(success=True)


@bp.route("/api/account/totp/disable", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def totp_disable():
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (current_user.id,)).fetchone()
    if not row["totp_secret"]:
        return jsonify(success=False, error="Two-factor authentication is not on."), 400
    if not check_password_hash(row["password"], str(data.get("password") or "")) or             totp.verify(row["totp_secret"], data.get("code"), row["totp_last_step"]) is None:
        log_event("totp_disable_failed", logging.WARNING, user=current_user.username)
        return jsonify(success=False, error="Wrong password or code."), 400
    db.execute("UPDATE users SET totp_secret = NULL, totp_pending_secret = NULL WHERE id = ?", (current_user.id,))
    db.commit()
    log_event("totp_disabled", user=current_user.username)
    return jsonify(success=True)
