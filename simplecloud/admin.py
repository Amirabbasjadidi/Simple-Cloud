import functools
import secrets

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from . import quota, system, users
from .db import get_db
from .events import log_event

bp = Blueprint("admin", __name__)


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(404)  # don't reveal the admin area
        return view(*args, **kwargs)
    return wrapper


def admin_api(view):
    """For the dashboard APIs: a signed-in non-admin gets 403 (JSON), not the 404 of the pages."""
    @functools.wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            return jsonify(success=False, error="Admins only."), 403
        return view(*args, **kwargs)
    return wrapper


def _user_or_404(user_id):
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _other_active_admins(db, user_id):
    return db.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND disabled = 0 AND id != ?",
                      (user_id,)).fetchone()[0]


def _quota(data):
    mb = data.get("quota_mb")
    if mb in (None, "", 0, "0"):
        return None
    try:
        mb = int(mb)
    except (TypeError, ValueError):
        raise users.UserError("Quota must be a whole number of MB.")
    if mb < 0:
        raise users.UserError("Quota must be a whole number of MB.")
    return mb * 1024 * 1024


@bp.route("/admin")
@admin_required
def admin_page():
    return render_template("admin.html")


@bp.route("/api/admin/users")
@admin_required
def list_users():
    rows = get_db().execute(
        "SELECT u.*, COUNT(f.id) AS file_count, COALESCE(SUM(f.size), 0) AS used_bytes "
        "FROM users u LEFT JOIN files f ON f.user_id = u.id GROUP BY u.id ORDER BY u.username COLLATE NOCASE"
    ).fetchall()
    return jsonify(users=[{
        "id": r["id"], "username": r["username"], "is_admin": bool(r["is_admin"]),
        "disabled": bool(r["disabled"]), "quota_bytes": r["quota_bytes"], "used_bytes": r["used_bytes"],
        "file_count": r["file_count"], "totp_enabled": bool(r["totp_secret"]),
        "must_change_password": bool(r["must_change_password"]), "is_self": r["id"] == current_user.id,
    } for r in rows])


@bp.route("/api/admin/users", methods=["POST"])
@admin_required
def create_user():
    data = request.get_json(silent=True) or {}
    try:
        quota = _quota(data)
        uid = users.create_user(get_db(), str(data.get("username") or ""), str(data.get("password") or ""),
                                is_admin=bool(data.get("is_admin")), quota_bytes=quota)
    except users.UserError as e:
        return jsonify(success=False, error=str(e)), 400
    if data.get("must_change_password", True):
        get_db().execute("UPDATE users SET must_change_password = 1 WHERE id = ?", (uid,))
        get_db().commit()
    log_event("admin_create_user", admin=current_user.username, user=data.get("username"))
    return jsonify(success=True, id=uid)


@bp.route("/api/admin/users/<int:user_id>", methods=["PATCH"])
@admin_required
def update_user(user_id):
    db = get_db()
    user = _user_or_404(user_id)
    data = request.get_json(silent=True) or {}
    is_self = user_id == current_user.id
    result = {"success": True}
    try:
        if "disabled" in data:
            disabled = bool(data["disabled"])
            if disabled and is_self:
                raise users.UserError("You cannot disable your own account.")
            if disabled and user["is_admin"] and not _other_active_admins(db, user_id):
                raise users.UserError("Cannot disable the last active admin.")
            db.execute("UPDATE users SET disabled = ?, session_version = session_version + ? WHERE id = ?",
                       (int(disabled), int(disabled), user_id))
        if "is_admin" in data:
            make_admin = bool(data["is_admin"])
            if not make_admin and is_self:
                raise users.UserError("You cannot remove your own admin role.")
            if not make_admin and not _other_active_admins(db, user_id):
                raise users.UserError("Cannot remove the last admin.")
            db.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(make_admin), user_id))
        if "quota_mb" in data:
            db.execute("UPDATE users SET quota_bytes = ? WHERE id = ?", (_quota(data), user_id))
        if data.get("reset_2fa"):
            db.execute("UPDATE users SET totp_secret = NULL, totp_pending_secret = NULL WHERE id = ?", (user_id,))
        db.commit()
        if data.get("reset_password"):
            temp = secrets.token_urlsafe(12)
            users.set_password(db, user_id, temp, must_change=True)
            result["temporary_password"] = temp  # shown once to the admin
    except users.UserError as e:
        db.rollback()
        return jsonify(success=False, error=str(e)), 400
    changes = sorted(k for k in data if k != "reset_password") + (["reset_password"] if data.get("reset_password") else [])
    log_event("admin_update_user", admin=current_user.username, user=user["username"], changes=",".join(changes))
    return jsonify(result)


@bp.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@admin_required
def delete_user(user_id):
    db = get_db()
    user = _user_or_404(user_id)
    data = request.get_json(silent=True) or {}
    if user_id == current_user.id:
        return jsonify(success=False, error="You cannot delete your own account."), 400
    if user["is_admin"] and not _other_active_admins(db, user_id):
        return jsonify(success=False, error="Cannot delete the last admin."), 400
    has_files = db.execute("SELECT 1 FROM files WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
    action = data.get("files") if has_files else None  # nothing to transfer: ignore files/to/new_user
    target = new_user = None
    if action == "transfer":
        spec = data.get("new_user")
        if isinstance(spec, dict):
            new_user = {"username": str(spec.get("username") or ""), "password": str(spec.get("password") or ""),
                        "must_change": bool(spec.get("must_change_password", True))}
        else:
            to = str(data.get("to") or "").strip()
            if not to:
                return jsonify(success=False, error="Choose a user to receive the files."), 400
            target = db.execute("SELECT * FROM users WHERE username = ?", (to,)).fetchone()
            if target is None:
                return jsonify(success=False, error="The user to receive the files does not exist."), 400
    try:
        count = users.delete_user(db, user, action, target, new_user)
    except users.UserError as e:
        return jsonify(success=False, error=str(e)), 400
    result = {"success": True}
    if action == "transfer":
        name = new_user["username"].strip() if new_user else target["username"]
        u = quota.usage(db, db.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0])
        result.update(receiver=name, over_quota=bool(u["quota"]) and u["used"] > u["quota"])
    log_event("admin_delete_user", admin=current_user.username, user=user["username"], files=action,
              count=count, to=result.get("receiver"), new_user=bool(new_user))
    return jsonify(result)


# ---------------------------------------------------------------- system / storage dashboard

@bp.route("/api/admin/system")
@admin_api
def system_stats():
    cfg = current_app.config
    data = dict(system.storage_stats(get_db(), cfg))
    data["backups"] = system.backup_status(cfg)
    data["backup_run"] = system.latest_run(get_db())
    return jsonify(data)


@bp.route("/api/admin/backup", methods=["GET"])
@admin_api
def backup_run_status():
    return jsonify(run=system.latest_run(get_db()))


@bp.route("/api/admin/backup", methods=["POST"])
@admin_api
def backup_now():
    cfg = current_app.config
    status = system.backup_status(cfg)
    if not status["writable"]:
        return jsonify(success=False, error="The backup folder is not writable by the Simple Cloud service.",
                       folder=status["folder"]), 409
    try:
        run_id = system.start_backup(get_db(), cfg, current_user.username, current_app.logger)
    except system.BackupBusy as busy:
        return jsonify(success=False, error="A backup is already running.", run=busy.args[0]), 409
    log_event("admin_backup_start", admin=current_user.username, run=run_id)
    return jsonify(success=True, run=system.latest_run(get_db())), 202
