"""Server CLI: `flask <command> --help`. Every prompt has an option so commands also run non-interactively.

Passwords: --password, the SIMPLECLOUD_PASSWORD env var, or a hidden prompt (click uses getpass).
"""
import click
from flask import current_app
from flask.cli import with_appcontext

from . import migrate as migrations
from . import users
from .db import LATEST_VERSION, get_db, schema_version
from .events import log_event


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
@click.option("--admin", is_flag=True, help="Give the user the admin role.")
@click.option("--quota", "quota_mb", type=int, help="Storage quota in MB (default: DEFAULT_QUOTA_BYTES).")
@with_appcontext
def add_user(username, password, admin, quota_mb):
    """Add a user."""
    try:
        users.create_user(_db(), username, password, is_admin=admin,
                          quota_bytes=quota_mb * 1024 * 1024 if quota_mb else None)
    except users.UserError as e:
        raise click.ClickException(str(e))
    log_event("cli_adduser", user=username, admin=admin)
    click.echo(f"User {username} added.")


@click.command("changepassword")
@click.option("--username", prompt=True)
@password_option
@click.option("--force-change/--no-force-change", default=False, help="Require a change at next login.")
@with_appcontext
def change_password(username, password, force_change):
    """Set a user's password (signs out their sessions)."""
    db = _db()
    try:
        users.set_password(db, _user(db, username)["id"], password, must_change=force_change)
    except users.UserError as e:
        raise click.ClickException(str(e))
    log_event("cli_changepassword", user=username)
    click.echo(f"Password for {username} updated.")


@click.command("setadmin")
@click.option("--username", prompt=True)
@click.option("--admin/--no-admin", default=True)
@with_appcontext
def set_admin(username, admin):
    """Grant or remove the admin role."""
    db = _db()
    db.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(admin), _user(db, username)["id"]))
    db.commit()
    log_event("cli_setadmin", user=username, admin=admin)
    click.echo(f"{username} is {'now' if admin else 'no longer'} an admin.")


@click.command("disable2fa")
@click.option("--username", prompt=True)
@with_appcontext
def disable_2fa(username):
    """Turn off two-factor authentication for a user (lost device)."""
    db = _db()
    db.execute("UPDATE users SET totp_secret = NULL, totp_pending_secret = NULL WHERE id = ?",
               (_user(db, username)["id"],))
    db.commit()
    log_event("cli_disable2fa", user=username)
    click.echo(f"Two-factor authentication disabled for {username}.")


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
    log_event("cli_changeusername", old=old_username, new=new_username)
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

    try:
        users.delete_user(db, user, files_action, target_row)
    except users.UserError as e:
        raise click.ClickException(str(e))
    log_event("cli_deleteuser", user=username, files=files_action, count=count)
    click.echo(summary + ": done.")


@click.command("listusers")
@with_appcontext
def list_users():
    """List users."""
    rows = _db().execute(
        "SELECT u.id, u.username, u.is_admin, u.disabled, COUNT(f.id) AS n, COALESCE(SUM(f.size), 0) AS bytes "
        "FROM users u LEFT JOIN files f ON f.user_id = u.id GROUP BY u.id ORDER BY u.id").fetchall()
    if not rows:
        click.echo("No users found.")
    for r in rows:
        flags = (" [admin]" if r["is_admin"] else "") + (" [disabled]" if r["disabled"] else "")
        click.echo(f"ID: {r['id']}  Username: {r['username']}{flags}  Files: {r['n']}  Bytes: {r['bytes']}")


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
    for cmd in (add_user, change_password, set_admin, disable_2fa, change_username, delete_user, list_users,
                migrate_cmd):
        app.cli.add_command(cmd)
