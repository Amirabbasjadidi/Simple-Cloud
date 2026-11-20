import sqlite3

from conftest import upload


def set_quota(app, username, quota):
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("UPDATE users SET quota_bytes = ? WHERE username = ?", (quota, username))
    conn.commit()
    conn.close()


def test_usage_endpoint(two_users, app):
    alice, _ = two_users
    upload(alice, "a.bin", b"x" * 100)
    assert alice.get("/api/usage").json == {"used": 100, "reserved": 0, "quota": None}


def test_upload_over_quota_rejected_and_not_stored(two_users, app):
    alice, _ = two_users
    set_quota(app, "alice", 150)
    assert upload(alice, "a.bin", b"x" * 100).json["success"]
    r = upload(alice, "b.bin", b"x" * 100)
    assert r.status_code == 507 and not r.json["success"]
    assert [f["name"] for f in alice.get("/api/files").json["files"]] == ["a.bin"]
    import os
    assert len(os.listdir(os.path.join(app.config["UPLOAD_FOLDER"], "2"))) == 1  # rejected file removed


def test_large_body_rejected_before_reading(two_users, app):
    alice, _ = two_users
    set_quota(app, "alice", 1000)
    r = upload(alice, "big.bin", b"x" * 200_000)
    assert r.status_code == 507


def test_quota_is_per_user(two_users, app):
    alice, bob = two_users
    set_quota(app, "alice", 10)
    assert upload(bob, "a.bin", b"x" * 100).json["success"]
