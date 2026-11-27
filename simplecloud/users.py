"""User operations shared by the web admin panel and the CLI."""
from flask import current_app
from werkzeug.security import generate_password_hash

from . import storage
from .i18n import gettext as _


class UserError(Exception):
    pass


def validate_password(password):
    if len(password or "") < current_app.config["MIN_PASSWORD_LENGTH"]:
        raise UserError(_("Password must be at least %(n)s characters.", n=current_app.config['MIN_PASSWORD_LENGTH']))


def _check_new_user(db, username, password):
    """The rules for a new account; returns the cleaned username."""
    username = (username or "").strip()
    if not username or len(username) > 64 or any(c.isspace() for c in username):
        raise UserError("Username must be 1-64 characters without spaces.")
    validate_password(password)
    if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        raise UserError(_("User %(name)s already exists.", name=repr(username)))
    return username


def _insert_user(db, username, password, is_admin=False, quota_bytes=None, must_change=False):
    """Validate and insert without committing, so callers can make it part of a larger transaction."""
    username = _check_new_user(db, username, password)
    if quota_bytes is None:
        quota_bytes = current_app.config["DEFAULT_QUOTA_BYTES"] or None
    return db.execute(
        "INSERT INTO users (username, password, is_admin, quota_bytes, must_change_password) VALUES (?, ?, ?, ?, ?)",
        (username, generate_password_hash(password), int(bool(is_admin)), quota_bytes, int(bool(must_change)))
    ).lastrowid


def create_user(db, username, password, is_admin=False, quota_bytes=None, must_change=False):
    uid = _insert_user(db, username, password, is_admin, quota_bytes, must_change)
    db.commit()
    return uid


def set_password(db, user_id, password, must_change=False):
    """Also bumps session_version, which logs out every existing session of that user."""
    validate_password(password)
    db.execute("UPDATE users SET password = ?, must_change_password = ?, session_version = session_version + 1 "
               "WHERE id = ?", (generate_password_hash(password), int(must_change), user_id))
    db.commit()


def _unique_root_folder(db, user_id, name):
    """`name`, or `name (2)`, `name (3)`... whichever is free among the user's top-level folders."""
    candidate, n = name, 1
    while db.execute("SELECT 1 FROM folders WHERE user_id = ? AND parent_id IS NULL AND name = ?",
                     (user_id, candidate)).fetchone():
        n += 1
        candidate = f"{name} ({n})"
    return candidate


def delete_user(db, user, files_action=None, target=None, new_user=None):
    """Delete a user in one transaction. Their files are deleted, or transferred to `target` (a user
    row) or to a new user created in the same transaction (`new_user`: dict with username, password,
    must_change). Transferred files keep their folder structure inside a new top-level folder
    "From <username>" of the receiver. Returns the number of files the user had.

    A user without files is simply deleted: files_action / target / new_user are ignored.
    """
    count = db.execute("SELECT COUNT(*) FROM files WHERE user_id = ?", (user["id"],)).fetchone()[0]
    transfer = bool(count) and files_action == "transfer"
    if count and files_action not in ("transfer", "delete"):
        raise UserError("The user has files: choose transfer or delete.")
    if transfer:
        if new_user is None and target is None:
            raise UserError("Choose a user to receive the files.")
        if target is not None and target["id"] == user["id"]:
            raise UserError("Cannot transfer files to the user being deleted.")
        if new_user is not None:  # fail early with a clear message; checked again under the lock
            _check_new_user(db, new_user.get("username"), new_user.get("password"))
    stored = []
    receiver = None
    try:
        db.execute("BEGIN IMMEDIATE")
        if transfer:
            receiver = target["id"] if new_user is None else _insert_user(
                db, new_user.get("username"), new_user.get("password"), must_change=new_user.get("must_change", True))
            root = db.execute(
                "INSERT INTO folders (user_id, parent_id, name) VALUES (?, NULL, ?)",
                (receiver, _unique_root_folder(db, receiver, _("From %(name)s", name=user["username"])))).lastrowid
            # Old top-level folders and files go under the new folder; deeper levels keep their parents.
            db.execute("UPDATE folders SET user_id = ?, parent_id = COALESCE(parent_id, ?) WHERE user_id = ?",
                       (receiver, root, user["id"]))
            # Trashed files stay in the receiver's trash (folder_id NULL there).
            db.execute("UPDATE files SET user_id = ?, folder_id = CASE WHEN deleted_at IS NULL "
                       "THEN COALESCE(folder_id, ?) END WHERE user_id = ?", (receiver, root, user["id"]))
            db.execute("UPDATE upload_links SET user_id = ? WHERE user_id = ?", (receiver, user["id"]))
        else:
            stored = [r[0] for r in db.execute("SELECT stored_name FROM files WHERE user_id = ?", (user["id"],))]
            stored += [s + ".thumb" for s in stored]
            db.execute("DELETE FROM files WHERE user_id = ?", (user["id"],))
            db.execute("DELETE FROM upload_links WHERE user_id = ?", (user["id"],))
        db.execute("DELETE FROM shares WHERE user_id = ?", (user["id"],))
        for table in ("uploads", "jobs"):  # partial uploads / fetch temp files
            stored += [r[0] for r in db.execute(f"SELECT stored_name FROM {table} WHERE user_id = ? "
                                                f"AND stored_name IS NOT NULL", (user["id"],))]
            db.execute(f"DELETE FROM {table} WHERE user_id = ?", (user["id"],))
        db.execute("DELETE FROM folders WHERE user_id = ?", (user["id"],))
        db.execute("DELETE FROM users WHERE id = ?", (user["id"],))
        db.commit()
    except Exception:
        db.rollback()
        raise
    # Disk files go only after the commit: a crash leaves unreferenced files, never rows without files.
    for name in stored:
        storage.remove_quietly(storage.abs_path(name))
    return count

