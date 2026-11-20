"""Server CLI: `flask <command> --help`. Every prompt has an option so commands also run non-interactively.

Passwords: --password, the SIMPLECLOUD_PASSWORD env var, or a hidden prompt (click uses getpass).
"""
import click
from flask import current_app
from flask.cli import with_appcontext
from werkzeug.security import generate_password_hash

from . import migrate as migrations
from . import storage
from .db import LATEST_VERSION, get_db, schema_version


def _db():
    db = get_db()
    if schema_version(db) != LATEST_VERSION:
        raise click.ClickException("Database schema is out of date. Run `flask migrate` first.")
    return db


def _user(db, username):
    row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        raise click.ClickException(f"User {username!r} not found.")
    return row


password_option = click.option(
    "--password", envvar="SIMPLECLOUD_PASSWORD", prompt=True, hide_input=True, confirmation_prompt=True,
    help="Password (prompted securely if omitted; or set SIMPLECLOUD_PASSWORD).")


@click.command("adduser")
@click.option("--username", prompt=True)
@password_option
@with_appcontext
def add_user(username, password):
    """Add a user."""
    db = _db()
    username = username.strip()
    if not username or not password:
        raise click.ClickException("Username and password must not be empty.")
    if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        raise click.ClickException(f"User {username!r} already exists.")
    db.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, generate_password_hash(password)))
    db.commit()
    current_app.logger.info("cli_adduser username=%r", username)
    click.echo(f"User {username} added.")


@click.command("changepassword")
@click.option("--username", prompt=True)
@password_option
@click.option("--force-change/--no-force-change", default=False, help="Require a change at next login.")
@with_appcontext
def change_password(username, password, force_change):
    """Set a user's password."""
    db = _db()
    user = _user(db, username)
    db.execute("UPDATE users SET password = ?, must_change_password = ? WHERE id = ?",
               (generate_password_hash(password), int(force_change), user["id"]))
    db.commit()
    current_app.logger.info("cli_changepassword username=%r", username)
    click.echo(f"Password for {username} updated.")


@click.command("changeusername")
@click.option("--old", "old_username", prompt="Current username")
@click.option("--new", "new_username", prompt="New username")
@with_appcontext
def change_username(old_username, new_username):
    """Rename a user. Files are linked by user id, so they follow automatically."""
    db = _db()
    user = _user(db, old_username)
    new_username = new_username.strip()
    if not new_username:
        raise click.ClickException("New username must not be empty.")
    if db.execute("SELECT 1 FROM users WHERE username = ?", (new_username,)).fetchone():
        raise click.ClickException(f"Username {new_username!r} already exists.")
    db.execute("UPDATE users SET username = ? WHERE id = ?", (new_username, user["id"]))
    db.commit()
    current_app.logger.info("cli_changeusername old=%r new=%r", old_username, new_username)
    click.echo(f"Username changed from {old_username} to {new_username}.")


@click.command("deleteuser")
@click.option("--username", prompt=True)
@click.option("--files", "files_action", type=click.Choice(["transfer", "delete"]),
              help="What to do with the user's files.")
@click.option("--to", "target", help="User that receives the files (with --files transfer).")
@click.option("--yes", is_flag=True, help="Don't ask for confirmation.")
@with_appcontext
def delete_user(username, files_action, target, yes):
    """Delete a user. Their files are transferred or deleted in the same transaction."""
    db = _db()
    user = _user(db, username)
    count = db.execute("SELECT COUNT(*) FROM files WHERE user_id = ?", (user["id"],)).fetchone()[0]

    if count and not files_action:
        files_action = click.prompt(f"{username} has {count} file(s). Transfer or delete them?",
                                    type=click.Choice(["transfer", "delete"]))
    if count and files_action == "transfer" and not target:
        target = click.prompt("Transfer files to which existing user")
    target_row = None
    if count and files_action == "transfer":
        target_row = _user(db, target)
        if target_row["id"] == user["id"]:
            raise click.ClickException("Cannot transfer files to the user being deleted.")

    summary = (f"Delete user {username!r}" +
               ("" if not count else f" and {'DELETE' if files_action == 'delete' else 'transfer'} "
                f"{count} file(s){' to ' + target if target_row else ''}"))
    if not yes and not click.confirm(summary + "?"):
        raise click.Abort()

    stored = []
    try:
        db.execute("BEGIN IMMEDIATE")
        if target_row:
            db.execute("UPDATE files SET user_id = ? WHERE user_id = ?", (target_row["id"], user["id"]))
        else:
            stored = [r["stored_name"] for r in db.execute("SELECT stored_name FROM files WHERE user_id = ?",
                                                           (user["id"],))]
            db.execute("DELETE FROM files WHERE user_id = ?", (user["id"],))
        db.execute("DELETE FROM users WHERE id = ?", (user["id"],))
        db.commit()
    except Exception:
        db.rollback()
        raise
    # Disk files go only after the commit: a crash leaves unreferenced files, never rows without files.
    for name in stored:
        storage.remove_quietly(storage.abs_path(name))
    current_app.logger.info("cli_deleteuser username=%r files=%s count=%d", username, files_action, count)
    click.echo(summary + ": done.")


@click.command("listusers")
@with_appcontext
def list_users():
    """List users."""
    rows = _db().execute(
        "SELECT u.id, u.username, COUNT(f.id) AS n, COALESCE(SUM(f.size), 0) AS bytes "
        "FROM users u LEFT JOIN files f ON f.user_id = u.id GROUP BY u.id ORDER BY u.id").fetchall()
    if not rows:
        click.echo("No users found.")
    for r in rows:
        click.echo(f"ID: {r['id']}  Username: {r['username']}  Files: {r['n']}  Bytes: {r['bytes']}")


@click.command("migrate")
@click.option("--dry-run", is_flag=True, help="Print planned changes without changing anything.")
@with_appcontext
def migrate_cmd(dry_run):
    """Upgrade the database and file storage (backs up first)."""
    try:
        migrations.run(current_app.config, dry_run=dry_run, echo=click.echo, logger=current_app.logger)
    except migrations.MigrationError as e:
        raise click.ClickException(str(e))


def register(app):
    for cmd in (add_user, change_password, change_username, delete_user, list_users, migrate_cmd):
        app.cli.add_command(cmd)
