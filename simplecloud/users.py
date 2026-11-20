"""User operations shared by the web admin panel and the CLI."""
from flask import current_app
from werkzeug.security import generate_password_hash

from . import storage


class UserError(Exception):
    pass


def validate_password(password):
    if len(password or "") < current_app.config["MIN_PASSWORD_LENGTH"]:
        raise UserError(f"Password must be at least {current_app.config['MIN_PASSWORD_LENGTH']} characters.")


def create_user(db, username, password, is_admin=False, quota_bytes=None):
    username = (username or "").strip()
    if not username or len(username) > 64 or any(c.isspace() for c in username):
        raise UserError("Username must be 1-64 characters without spaces.")
    validate_password(password)
    if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        raise UserError(f"User {username!r} already exists.")
    if quota_bytes is None:
        quota_bytes = current_app.config["DEFAULT_QUOTA_BYTES"] or None
    cur = db.execute("INSERT INTO users (username, password, is_admin, quota_bytes) VALUES (?, ?, ?, ?)",
                     (username, generate_password_hash(password), int(bool(is_admin)), quota_bytes))
    db.commit()
    return cur.lastrowid


def set_password(db, user_id, password, must_change=False):
    """Also bumps session_version, which logs out every existing session of that user."""
    validate_password(password)
    db.execute("UPDATE users SET password = ?, must_change_password = ?, session_version = session_version + 1 "
               "WHERE id = ?", (generate_password_hash(password), int(must_change), user_id))
    db.commit()


def delete_user(db, user, files_action=None, target=None):
    """Delete a user; their files are transferred to `target` (a user row) or deleted, in one transaction."""
    count = db.execute("SELECT COUNT(*) FROM files WHERE user_id = ?", (user["id"],)).fetchone()[0]
    if count and files_action not in ("transfer", "delete"):
        raise UserError("The user has files: choose transfer or delete.")
    if count and files_action == "transfer":
        if target is None:
            raise UserError("Choose a user to receive the files.")
        if target["id"] == user["id"]:
            raise UserError("Cannot transfer files to the user being deleted.")
    stored = []
    try:
        db.execute("BEGIN IMMEDIATE")
        if count and files_action == "transfer":
            db.execute("UPDATE files SET user_id = ?, folder_id = NULL WHERE user_id = ?", (target["id"], user["id"]))
        else:
            stored = [r[0] for r in db.execute("SELECT stored_name FROM files WHERE user_id = ?", (user["id"],))]
            db.execute("DELETE FROM files WHERE user_id = ?", (user["id"],))
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

