"""User management CLI (flask <command>)."""
import os
import sqlite3

from flask import current_app
from werkzeug.security import generate_password_hash

from .db import get_db


def register(app):
    app.cli.add_command(add_user)
    app.cli.add_command(change_password)
    app.cli.add_command(change_username)
    app.cli.add_command(delete_user)
    app.cli.add_command(list_users)
    app.cli.add_command(helpme)


class _Commands:
    @staticmethod
    def command(name):
        import click
        from flask.cli import with_appcontext
        return lambda f: click.command(name)(with_appcontext(f))


cli = _Commands()


def add_user_helper(username, password):
    hashed_password = generate_password_hash(password)
    conn = get_db()
    if True:
        try:
            conn.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, hashed_password))
            conn.commit()
            print(f"User {username} added successfully.")
        except sqlite3.IntegrityError:
            print(f"User {username} already exists.")


@cli.command('adduser')
def add_user():
    username = input("Enter username: ")
    password = input("Enter password: ")
    add_user_helper(username, password)

@cli.command('changepassword')
def change_password():
    username = input("Enter username: ")
    new_password = input("Enter new password: ")
    hashed_password = generate_password_hash(new_password)
    conn = get_db()
    if True:
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if user:
            conn.execute('UPDATE users SET password = ? WHERE username = ?', (hashed_password, username))
            conn.commit()
            print(f"Password for user {username} updated successfully.")
        else:
            print(f"User {username} not found.")


@cli.command('changeusername')
def change_username():
    old_username = input("Enter current username: ")
    new_username = input("Enter new username: ")

    conn = get_db()
    if True:
        user = conn.execute('SELECT * FROM users WHERE username = ?', (old_username,)).fetchone()
        if not user:
            print(f"User {old_username} not found.")
            return

        existing_user = conn.execute('SELECT * FROM users WHERE username = ?', (new_username,)).fetchone()
        if existing_user:
            print(f"Error: The username {new_username} already exists. Please choose another one.")
            return

        try:
            conn.execute('UPDATE users SET username = ? WHERE username = ?', (new_username, old_username))
            conn.commit()

            if conn.total_changes > 0:
                print(f"Username changed from {old_username} to {new_username} successfully.")
                print("Updating uploaded files...")

                conn.execute('UPDATE files SET uploaded_by = ? WHERE uploaded_by = ?', (new_username, old_username))
                conn.commit()

                if conn.total_changes > 0:
                    print("Uploaded files updated successfully.")
                else:
                    print("No uploaded files found for this user.")

            else:
                print("Failed to change username. No changes detected.")

        except sqlite3.Error as e:
            print(f"Database error: {e}")


@cli.command('deleteuser')
def delete_user():
    username = input("Enter username to delete: ")
    conn = get_db()
    if True:
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if not user:
            print(f"User {username} not found.")
            return

        conn.execute('DELETE FROM users WHERE username = ?', (username,))
        conn.commit()
        print(f"User {username} deleted successfully.")

        while True:
            new_owner = input(
                "Enter an existing username to transfer files to, or type 'DELETE_FILES' to remove all files uploaded by this user: ")

            if new_owner == "DELETE_FILES":
                files = conn.execute('SELECT path FROM files WHERE uploaded_by = ?', (username,)).fetchall()

                for file in files:
                    file_path = file[0]
                    if os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                        except Exception as e:
                            print(f"Error deleting file {file_path}: {e}")

                conn.execute('DELETE FROM files WHERE uploaded_by = ?', (username,))
                conn.commit()
                print(f"All files uploaded by {username} have been deleted from the server and database.")
                return

            existing_user = conn.execute('SELECT * FROM users WHERE username = ?', (new_owner,)).fetchone()
            if existing_user:
                conn.execute('UPDATE files SET uploaded_by = ? WHERE uploaded_by = ?', (new_owner, username))
                conn.commit()
                print(f"All files uploaded by {username} have been transferred to {new_owner}.")
                return
            else:
                create_new = input(
                    f"User {new_owner} does not exist. Do you want to create it? (y/n): ").strip().lower()
                if create_new == 'y':
                    new_password = input(f"Enter password for {new_owner}: ")
                    add_user_helper(new_owner, new_password)
                    conn.execute('UPDATE files SET uploaded_by = ? WHERE uploaded_by = ?', (new_owner, username))
                    conn.commit()
                    print(f"User {new_owner} created and all files from {username} transferred.")
                    return
                elif create_new == 'n':
                    print("Please enter a valid username to transfer files to.")
                else:
                    print("Invalid input. Please enter 'y' or 'n'.")

@cli.command('listusers')
def list_users():
    conn = get_db()
    if True:
        users = conn.execute('SELECT id, username FROM users').fetchall()
        conn.commit()

        if users:
            print("Registered users:")
            for user in users:
                print(f"ID: {user['id']}, Username: {user['username']}")
        else:
            print("No users found.")

    conn = get_db()
    if True:
        conn.execute('VACUUM;')


@cli.command('help')
def helpme():
    commands = {
        "adduser": "Adds a new user to the database.",
        "changepassword": "Changes the password for an existing user.",
        "changeusername": "Updates the username of an existing user and transfers associated files.",
        "deleteuser": "Deletes a user and allows file transfer or removal.",
        "listusers": "Lists all registered users.",
    }

    print("Available CLI commands:")
    for cmd, desc in commands.items():
        print(f"  flask {cmd} - {desc}")


