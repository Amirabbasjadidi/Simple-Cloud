import sqlite3
import time

from conftest import add_user, login


def make_link(client, **opts):
    r = client.post("/api/upload-links", json=opts)
    assert r.json["success"], r.json
    return r.json["link"], r.json["link"]["url"].rsplit("/", 1)[1]


def send(client, token, name, data):
    """Chunked upload through a link; returns the final JSON."""
    r = client.post(f"/r/{token}/uploads", json={"name": name, "size": len(data)})
    if r.status_code != 200 or "file" in r.json:
        return r
    return client.put(f"/r/{token}/uploads/{r.json['upload_id']}?offset=0", data=data,
                      content_type="application/octet-stream")


def test_guest_uploads_into_folder(two_users, app):
    alice, _ = two_users
    folder = alice.post("/api/folders", json={"name": "Inbox"}).json["folder"]["id"]
    link, t = make_link(alice, folder_id=folder)
    assert link["folder"] == "Inbox"
    guest = app.test_client()
    assert guest.get(f"/r/{t}").status_code == 200
    f = send(guest, t, "doc.txt", b"hello").json["file"]
    assert f["folder_id"] == folder and f["via_link"] and f["uploaded_by"] is None
    assert alice.get("/api/usage").json["used"] == 5  # owner's quota
    assert alice.get("/api/upload-links").json["links"][0]["file_count"] == 1


def test_login_required_records_username(two_users, app):
    alice, bob = two_users
    _, t = make_link(alice, login_required=True)
    guest = app.test_client()
    assert guest.post(f"/r/{t}/uploads", json={"name": "a", "size": 1}).status_code == 401
    assert "/login" in guest.get(f"/r/{t}").get_data(as_text=True)
    f = send(bob, t, "from-bob.txt", b"x").json["file"]
    assert f["uploaded_by"] == "bob"
    names = {x["name"]: x for x in alice.get("/api/files").json["files"]}
    assert names["from-bob.txt"]["uploaded_by"] == "bob"
    assert bob.get("/api/files").json["files"] == []  # it is alice's file


def test_password_expiry_file_limit_and_revoke(two_users, app, monkeypatch):
    alice, _ = two_users
    link, t = make_link(alice, password="pw", max_files=1, expires_hours=1)
    guest = app.test_client()
    assert send(guest, t, "a", b"1").status_code == 403
    assert guest.post(f"/r/{t}", json={"password": "bad"}).status_code == 403
    assert guest.post(f"/r/{t}", json={"password": "pw"}).json["success"]
    assert send(guest, t, "a", b"1").status_code == 200
    assert send(guest, t, "b", b"2").status_code == 410  # file limit
    assert guest.get(f"/r/{t}").status_code == 410
    _, t2 = make_link(alice, expires_hours=1)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3601)
    assert send(guest, t2, "c", b"3").status_code == 410
    monkeypatch.setattr(time, "time", real)
    assert alice.delete(f"/api/upload-links/{link['id']}").json["success"]
    assert guest.get(f"/r/{t}").status_code == 404


def test_link_uploads_are_private_to_the_link(two_users, app):
    """Another link's (or the owner's own) upload ids don't work through a link."""
    alice, _ = two_users
    _, t1 = make_link(alice)
    _, t2 = make_link(alice)
    guest = app.test_client()
    uid = guest.post(f"/r/{t1}/uploads", json={"name": "a", "size": 3}).json["upload_id"]
    assert guest.put(f"/r/{t2}/uploads/{uid}?offset=0", data=b"abc").status_code == 404
    assert alice.put(f"/api/uploads/{uid}?offset=0", data=b"abc").status_code == 404
    own = alice.post("/api/uploads", json={"name": "o", "size": 3}).json["upload_id"]
    assert guest.put(f"/r/{t1}/uploads/{own}?offset=0", data=b"abc").status_code == 404


def test_disabled_owner_and_quota(two_users, app):
    alice, _ = two_users
    _, t = make_link(alice)
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("UPDATE users SET quota_bytes = 2 WHERE username = 'alice'")
    conn.commit()
    guest = app.test_client()
    assert send(guest, t, "big", b"12345").status_code == 507
    conn.execute("UPDATE users SET disabled = 1 WHERE username = 'alice'")
    conn.commit()
    assert guest.get(f"/r/{t}").status_code == 404
