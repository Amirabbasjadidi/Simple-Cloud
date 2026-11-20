import sqlite3
import time

from conftest import upload


def make_share(client, **opts):
    fid = upload(client, "گزارش.pdf", b"%PDF-data").json["id"]
    r = client.post(f"/api/files/{fid}/shares", json=opts)
    assert r.json["success"], r.json
    return fid, r.json["share"]


def token(share):
    return share["url"].rsplit("/", 1)[1]


def test_public_download_without_login(two_users, app):
    alice, _ = two_users
    _, share = make_share(alice)
    anon = app.test_client()
    page = anon.get(f"/s/{token(share)}")
    assert page.status_code == 200 and "گزارش.pdf" in page.get_data(as_text=True)
    assert page.headers["X-Robots-Tag"].startswith("noindex")
    r = anon.get(f"/s/{token(share)}/download")
    assert r.data == b"%PDF-data" and r.headers["Content-Disposition"].startswith("attachment")


def test_only_owner_can_share_list_and_revoke(two_users):
    alice, bob = two_users
    fid, share = make_share(alice)
    assert bob.post(f"/api/files/{fid}/shares", json={}).status_code == 404
    assert bob.get("/api/shares").json["shares"] == []
    assert bob.delete(f"/api/shares/{share['id']}").status_code == 404
    assert len(alice.get("/api/shares").json["shares"]) == 1


def test_expiry(two_users, app, monkeypatch):
    alice, _ = two_users
    _, share = make_share(alice, expires_hours=1)
    anon = app.test_client()
    assert anon.get(f"/s/{token(share)}").status_code == 200
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3601)
    assert anon.get(f"/s/{token(share)}").status_code == 410
    assert anon.get(f"/s/{token(share)}/download").status_code == 410
    assert alice.get("/api/shares").json["shares"][0]["expired"] is True


def test_password(two_users, app):
    alice, _ = two_users
    _, share = make_share(alice, password="open sesame")
    anon = app.test_client()
    t = token(share)
    assert anon.get(f"/s/{t}/download").status_code == 403
    assert anon.post(f"/s/{t}", json={"password": "wrong"}).status_code == 403
    assert anon.post(f"/s/{t}", json={"password": "open sesame"}).json["success"]
    assert anon.get(f"/s/{t}/download").data == b"%PDF-data"
    assert app.test_client().get(f"/s/{t}/download").status_code == 403  # unlock is per visitor


def test_download_limit(two_users, app):
    alice, _ = two_users
    _, share = make_share(alice, max_downloads=2)
    anon = app.test_client()
    t = token(share)
    assert anon.get(f"/s/{t}/download").status_code == 200
    assert anon.get(f"/s/{t}/download", headers={"Range": "bytes=3-"}).status_code == 206  # resume: not counted
    assert anon.get(f"/s/{t}/download").status_code == 200
    assert anon.get(f"/s/{t}/download").status_code == 410
    assert alice.get("/api/shares").json["shares"][0]["download_count"] == 2


def test_revoke_and_file_delete(two_users, app):
    alice, _ = two_users
    fid, share = make_share(alice)
    _, share2 = make_share(alice)
    anon = app.test_client()
    assert alice.delete(f"/api/shares/{share['id']}").json["success"]
    assert anon.get(f"/s/{token(share)}").status_code == 404
    # deleting the file removes its links
    fid2 = share2["file_id"]
    alice.delete(f"/api/files/{fid2}")
    assert anon.get(f"/s/{token(share2)}").status_code == 404
    assert alice.get("/api/shares").json["shares"] == []


def test_disabled_owner_links_stop(two_users, app):
    alice, _ = two_users
    _, share = make_share(alice)
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("UPDATE users SET disabled = 1 WHERE username = 'alice'")
    conn.commit()
    assert app.test_client().get(f"/s/{token(share)}").status_code == 404


def test_invalid_options(two_users):
    alice, _ = two_users
    fid = upload(alice, "a.txt").json["id"]
    for bad in ({"expires_hours": -1}, {"expires_hours": "x"}, {"max_downloads": 0}):
        assert alice.post(f"/api/files/{fid}/shares", json=bad).status_code == 400


def test_unknown_token(app):
    assert app.test_client().get("/s/not-a-real-token").status_code == 404
