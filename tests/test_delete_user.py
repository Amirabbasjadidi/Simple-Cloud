"""Deleting a user from the admin panel: no files, transfer (existing or new user), delete."""
import os
import sqlite3

from conftest import add_user, login, upload
from test_admin import setup


def q(app, sql, *params):
    conn = sqlite3.connect(app.config["DATABASE"])
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def folder(client, name, parent=None):
    r = client.post("/api/folders", json={"name": name, "parent_id": parent})
    assert r.json["success"], r.json
    return r.json["folder"]["id"]


def put(client, fid, folder_id):
    assert client.patch(f"/api/files/{fid}", json={"folder_id": folder_id}).json["success"]


def tree(client, folder_id=None, prefix=""):
    """Every file of a user as 'path/name', walking the folders through the API."""
    d = client.get("/api/files" + (f"?folder={folder_id}" if folder_id else "")).json
    out = [prefix + f["name"] for f in d["files"]]
    for sub in d["folders"]:
        out += tree(client, sub["id"], f"{prefix}{sub['name']}/")
    return sorted(out)


def alice_with_files(alice):
    """alice: root.txt, Docs/a.txt, Docs/2026/b.txt."""
    docs = folder(alice, "Docs")
    year = folder(alice, "2026", docs)
    upload(alice, "root.txt", b"r")
    put(alice, upload(alice, "a.txt", b"aa").json["id"], docs)
    put(alice, upload(alice, "b.txt", b"bbb").json["id"], year)
    assert tree(alice) == ["Docs/2026/b.txt", "Docs/a.txt", "root.txt"]


def delete(boss, uid, **body):
    return boss.delete(f"/api/admin/users/{uid}", json=body)


def test_delete_user_without_files(app):
    boss, _alice, users = setup(app)
    r = delete(boss, users["alice"]["id"])  # what the dialog sends: nothing
    assert r.status_code == 200 and r.json["success"], r.json
    assert not q(app, "SELECT 1 FROM users WHERE username = 'alice'")
    # the old bug: a stale "transfer" with an empty target is ignored when there is nothing to move
    add_user(app, "carol")
    carol_id = q(app, "SELECT id FROM users WHERE username = 'carol'")[0][0]
    assert delete(boss, carol_id, files="transfer", to="").json["success"]


def test_transfer_to_existing_user_keeps_structure(app):
    boss, alice, users = setup(app)
    alice_with_files(alice)
    folder(boss, "From alice")  # an existing folder with the same name must not clash
    boss.set_cookie("lang", "en")
    r = delete(boss, users["alice"]["id"], files="transfer", to="boss")
    assert r.json == {"success": True, "receiver": "boss", "over_quota": False}
    assert tree(boss) == ["From alice (2)/Docs/2026/b.txt", "From alice (2)/Docs/a.txt", "From alice (2)/root.txt"]
    assert not q(app, "SELECT 1 FROM users WHERE username = 'alice'")
    assert not q(app, "SELECT 1 FROM folders WHERE user_id = ?", users["alice"]["id"])
    # contents intact
    f = boss.get("/api/files?q=b.txt").json["files"][0]
    assert boss.get(f["download_url"]).data == b"bbb"


def test_transfer_folder_name_is_in_the_admins_language(app):
    boss, alice, users = setup(app)
    upload(alice, "x.txt")
    assert delete(boss, users["alice"]["id"], files="transfer", to="boss").json["success"]
    assert [f["name"] for f in boss.get("/api/files").json["folders"]] == ["از alice"]


def test_transfer_to_new_user(app):
    boss, alice, users = setup(app)
    alice_with_files(alice)
    r = delete(boss, users["alice"]["id"], files="transfer",
               new_user={"username": "dana", "password": "dana-pass-1", "must_change_password": True})
    assert r.json["success"] and r.json["receiver"] == "dana", r.json
    assert not q(app, "SELECT 1 FROM users WHERE username = 'alice'")
    (must_change, quota), = q(app, "SELECT must_change_password, quota_bytes FROM users WHERE username = 'dana'")
    assert must_change == 1 and quota is None  # DEFAULT_QUOTA_BYTES = 0: unlimited
    dana = app.test_client()
    assert login(app, dana, "dana", password="dana-pass-1").json["success"]
    dana.post("/api/account/password", json={"current_password": "dana-pass-1", "new_password": "dana-pass-2"})
    assert tree(dana) == ["از alice/Docs/2026/b.txt", "از alice/Docs/a.txt", "از alice/root.txt"]


def test_new_user_without_forced_change_and_default_quota(make_app):
    app = make_app(DEFAULT_QUOTA_BYTES=5 * 1024 * 1024)
    boss, alice, users = setup(app)
    upload(alice, "x.txt")
    assert delete(boss, users["alice"]["id"], files="transfer", new_user={
        "username": "erin", "password": "erin-pass-1", "must_change_password": False}).json["success"]
    assert q(app, "SELECT must_change_password, quota_bytes FROM users WHERE username = 'erin'") == [(0, 5 * 1024 * 1024)]


def snapshot(app):
    return (q(app, "SELECT id, username FROM users ORDER BY id"),
            q(app, "SELECT id, user_id, folder_id FROM files ORDER BY id"),
            q(app, "SELECT id, user_id, parent_id, name FROM folders ORDER BY id"))


def test_new_user_failures_change_nothing(app):
    boss, alice, users = setup(app)
    alice_with_files(alice)
    before = snapshot(app)
    boss.set_cookie("lang", "en")
    cases = [
        ({"username": "boss", "password": "long-enough-1"}, "already exists"),
        ({"username": "", "password": "long-enough-1"}, "Username must be"),
        ({"username": "has space", "password": "long-enough-1"}, "Username must be"),
        ({"username": "frank", "password": "short"}, "at least 8 characters"),
    ]
    for spec, message in cases:
        r = delete(boss, users["alice"]["id"], files="transfer", new_user=spec)
        assert r.status_code == 400 and message in r.json["error"], (spec, r.json)
        assert snapshot(app) == before, spec


def test_failure_inside_the_transaction_rolls_back(app, monkeypatch):
    """Even a failure after the new user and folder were inserted leaves no trace."""
    boss, alice, users = setup(app)
    alice_with_files(alice)
    before = snapshot(app)
    import simplecloud.users as users_mod
    real = users_mod._unique_root_folder

    def boom(*a, **k):
        real(*a, **k)
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(users_mod, "_unique_root_folder", boom)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    r = delete(boss, users["alice"]["id"], files="transfer", new_user={"username": "gina", "password": "gina-pass-1"})
    assert r.status_code == 500
    assert snapshot(app) == before
    assert not q(app, "SELECT 1 FROM users WHERE username = 'gina'")


def test_transfer_target_errors(app):
    boss, alice, users = setup(app)
    upload(alice, "x.txt")
    for body, message in (
        ({"files": "transfer", "to": ""}, "Choose a user to receive the files."),
        ({"files": "transfer", "to": "nobody"}, "The user to receive the files does not exist."),
        ({"files": "transfer", "to": "alice"}, "Cannot transfer files to the user being deleted."),
        ({}, "The user has files: choose transfer or delete."),
    ):
        r = delete(boss, users["alice"]["id"], **body)
        assert r.status_code == 400 and r.json["error"] == message, (body, r.json)
    assert q(app, "SELECT COUNT(*) FROM users WHERE username = 'alice'")[0][0] == 1
    assert len(alice.get("/api/files").json["files"]) == 1


def test_delete_permanently(app):
    boss, alice, users = setup(app)
    alice_with_files(alice)
    uid = users["alice"]["id"]
    assert delete(boss, uid, files="delete", to="boss").json["success"]  # "to" is ignored
    assert not q(app, "SELECT 1 FROM files") and not q(app, "SELECT 1 FROM folders")
    assert os.listdir(os.path.join(app.config["UPLOAD_FOLDER"], str(uid))) == []
    assert tree(boss) == []


def test_over_quota_receiver_is_allowed_and_reported(app):
    boss, alice, users = setup(app)
    upload(alice, "big.bin", b"x" * 3000)
    add_user(app, "hank")
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("UPDATE users SET quota_bytes = 1000 WHERE username = 'hank'")
    conn.commit()
    conn.close()
    r = delete(boss, users["alice"]["id"], files="transfer", to="hank")
    assert r.json == {"success": True, "receiver": "hank", "over_quota": True}
    hank = next(u for u in boss.get("/api/admin/users").json["users"] if u["username"] == "hank")
    assert hank["used_bytes"] == 3000 and hank["quota_bytes"] == 1000
