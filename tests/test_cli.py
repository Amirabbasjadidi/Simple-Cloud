import os
import sqlite3

from conftest import add_user, login, upload


def run(app, *args, **kw):
    return app.test_cli_runner().invoke(args=list(args), **kw)


def q(app, sql, *params):
    conn = sqlite3.connect(app.config["DATABASE"])
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_adduser_noninteractive_and_prompt(app):
    r = run(app, "adduser", "--username", "carol", "--password", "secret-one")
    assert r.exit_code == 0, r.output
    r = run(app, "adduser", input="dave\nsecret-two\nsecret-two\n")
    assert r.exit_code == 0, r.output
    assert "secret-two" not in r.output  # hidden prompt
    assert {u for (u,) in q(app, "SELECT username FROM users")} >= {"carol", "dave"}
    assert run(app, "adduser", "--username", "carol", "--password", "x").exit_code != 0


def test_adduser_password_from_env(app):
    r = run(app, "adduser", "--username", "erin", env={"SIMPLECLOUD_PASSWORD": "env-password"})
    assert r.exit_code == 0, r.output
    c = app.test_client()
    assert login(app, c, "erin", "env-password").json["success"]


def test_changepassword(app):
    add_user(app, "alice")
    assert run(app, "changepassword", "--username", "alice", "--password", "new-password").exit_code == 0
    assert login(app, app.test_client(), "alice", "new-password").json["success"]
    assert run(app, "changepassword", "--username", "nobody", "--password", "x").exit_code != 0


def test_changeusername_keeps_files(two_users, app):
    alice, _ = two_users
    upload(alice, "a.txt")
    r = run(app, "changeusername", "--old", "alice", "--new", "alicia")
    assert r.exit_code == 0 and "alicia" in r.output
    assert len(alice.get("/api/files").json["files"]) == 1  # same user id
    assert run(app, "changeusername", "--old", "alicia", "--new", "bob").exit_code != 0  # taken
    assert run(app, "changeusername", "--old", "ghost", "--new", "x").exit_code != 0


def test_deleteuser_transfer(two_users, app):
    alice, bob = two_users
    upload(alice, "a.txt", b"data")
    r = run(app, "deleteuser", "--username", "alice", "--files", "transfer", "--to", "bob", "--yes")
    assert r.exit_code == 0, r.output
    files = bob.get("/api/files").json["files"]
    assert [f["name"] for f in files] == ["a.txt"]
    assert bob.get(files[0]["download_url"]).data == b"data"


def test_deleteuser_transfer_to_missing_user_changes_nothing(two_users, app):
    alice, _ = two_users
    upload(alice, "a.txt")
    r = run(app, "deleteuser", "--username", "alice", "--files", "transfer", "--to", "nobody", "--yes")
    assert r.exit_code != 0
    assert q(app, "SELECT COUNT(*) FROM users WHERE username='alice'")[0][0] == 1
    assert len(alice.get("/api/files").json["files"]) == 1


def test_deleteuser_delete_files(two_users, app):
    alice, _ = two_users
    upload(alice, "a.txt")
    r = run(app, "deleteuser", "--username", "alice", "--files", "delete", "--yes")
    assert r.exit_code == 0, r.output
    assert q(app, "SELECT COUNT(*) FROM files")[0][0] == 0
    assert os.listdir(os.path.join(app.config["UPLOAD_FOLDER"], "2")) == []


def test_deleteuser_confirmation_abort(two_users, app):
    r = run(app, "deleteuser", "--username", "bob", input="n\n")
    assert r.exit_code != 0
    assert q(app, "SELECT COUNT(*) FROM users WHERE username='bob'")[0][0] == 1


def test_listusers(two_users, app):
    alice, _ = two_users
    upload(alice, "a.txt", b"12345")
    out = run(app, "listusers").output
    assert "alice" in out and "Files: 1" in out and "Bytes: 5" in out
