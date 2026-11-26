import sqlite3

from conftest import add_user, login, upload


def make_admin(app, username):
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("UPDATE users SET is_admin = 1 WHERE username = ?", (username,))
    conn.commit()
    conn.close()


def setup(app):
    add_user(app, "boss")
    make_admin(app, "boss")
    add_user(app, "alice")
    boss, alice = app.test_client(), app.test_client()
    assert login(app, boss, "boss").json["success"]
    assert login(app, alice, "alice").json["success"]
    users = {u["username"]: u for u in boss.get("/api/admin/users").json["users"]}
    return boss, alice, users


def test_non_admin_gets_404(app):
    _, alice, users = setup(app)
    assert alice.get("/admin").status_code == 404
    assert alice.get("/api/admin/users").status_code == 404
    assert alice.patch(f"/api/admin/users/{users['alice']['id']}", json={"is_admin": True}).status_code == 404


def test_create_user_must_change_password(app):
    boss, _, _ = setup(app)
    r = boss.post("/api/admin/users", json={"username": "carol", "password": "carol-pass", "quota_mb": 10})
    assert r.json["success"]
    carol = app.test_client()
    assert login(app, carol, "carol", "carol-pass").json["redirect_url"] == "/account"
    u = next(u for u in boss.get("/api/admin/users").json["users"] if u["username"] == "carol")
    assert u["quota_bytes"] == 10 * 1024 * 1024
    assert boss.post("/api/admin/users", json={"username": "carol", "password": "carol-pass"}).status_code == 400
    assert boss.post("/api/admin/users", json={"username": "dan", "password": "short"}).status_code == 400


def test_disable_signs_out_and_blocks_login(app):
    boss, alice, users = setup(app)
    assert boss.patch(f"/api/admin/users/{users['alice']['id']}", json={"disabled": True}).json["success"]
    assert alice.get("/files").status_code == 302
    assert login(app, app.test_client(), "alice").status_code == 401
    boss.patch(f"/api/admin/users/{users['alice']['id']}", json={"disabled": False})
    assert login(app, app.test_client(), "alice").json["success"]


def test_reset_password(app):
    boss, alice, users = setup(app)
    r = boss.patch(f"/api/admin/users/{users['alice']['id']}", json={"reset_password": True})
    temp = r.json["temporary_password"]
    assert alice.get("/files").status_code == 302  # old session gone
    assert login(app, app.test_client(), "alice", temp).json["redirect_url"] == "/account"


def test_cannot_lock_yourself_out(app):
    boss, _, users = setup(app)
    me = users["boss"]["id"]
    assert boss.patch(f"/api/admin/users/{me}", json={"disabled": True}).status_code == 400
    assert boss.patch(f"/api/admin/users/{me}", json={"is_admin": False}).status_code == 400
    assert boss.delete(f"/api/admin/users/{me}").status_code == 400


def test_delete_user_transfer_and_delete(app):
    boss, alice, users = setup(app)
    upload(alice, "a.txt", b"data")
    r = boss.delete(f"/api/admin/users/{users['alice']['id']}", json={"files": "transfer", "to": "nobody"})
    assert r.status_code == 400
    r = boss.delete(f"/api/admin/users/{users['alice']['id']}", json={})
    assert r.status_code == 400  # has files: must choose
    r = boss.delete(f"/api/admin/users/{users['alice']['id']}", json={"files": "transfer", "to": "boss"})
    assert r.json["success"]
    root = boss.get("/api/files").json
    assert root["files"] == [] and [f["name"] for f in root["folders"]] == ["از alice"]  # request language: fa
    inside = boss.get(f"/api/files?folder={root['folders'][0]['id']}").json
    assert [f["name"] for f in inside["files"]] == ["a.txt"]


def test_quota_validation(app):
    boss, _, users = setup(app)
    assert boss.patch(f"/api/admin/users/{users['alice']['id']}", json={"quota_mb": "abc"}).status_code == 400
    assert boss.patch(f"/api/admin/users/{users['alice']['id']}", json={"quota_mb": None}).json["success"]


def test_admin_page_renders_and_nav_link(app):
    boss, alice, _ = setup(app)
    assert b"Users" in boss.get("/admin").data
    assert b'href="/admin"' in boss.get("/account").data
    assert b'href="/admin"' not in alice.get("/account").data


def test_fetch_limit_setting_and_exemption(app):
    boss, alice, users = setup(app)
    assert boss.get("/api/admin/settings").json["fetch_limit_mb"] is None
    assert boss.patch("/api/admin/settings", json={"fetch_limit_mb": 500}).json["success"]
    assert boss.get("/api/admin/settings").json["fetch_limit_mb"] == 500
    assert boss.patch("/api/admin/settings", json={"fetch_limit_mb": -1}).status_code == 400
    assert boss.patch("/api/admin/settings", json={"fetch_limit_mb": None}).json["success"]
    assert boss.get("/api/admin/settings").json["fetch_limit_mb"] is None
    assert alice.patch("/api/admin/settings", json={"fetch_limit_mb": 1}).status_code == 403
    assert boss.patch(f"/api/admin/users/{users['alice']['id']}", json={"fetch_unlimited": True}).json["success"]
    users = {u["username"]: u for u in boss.get("/api/admin/users").json["users"]}
    assert users["alice"]["fetch_unlimited"] is True
