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
    assert app.test_client().get(f"/s/{t}/download").status_code == 403  # unlock is per visitor (same IP)
    assert _client(app, "10.0.0.9").get(f"/s/{t}/download").status_code == 403


def test_download_limit(two_users, app):
    alice, _ = two_users
    _, share = make_share(alice, max_downloads=2)
    anon = app.test_client()
    t = token(share)
    assert anon.get(f"/s/{t}/download").status_code == 200
    assert anon.get(f"/s/{t}/download", headers={"Range": "bytes=3-"}).status_code == 206  # same client: not counted
    assert anon.get(f"/s/{t}/download").status_code == 200  # retry within the grant: not counted
    assert alice.get("/api/shares").json["shares"][0]["download_count"] == 1
    assert _client(app, "10.0.0.2").get(f"/s/{t}/download").status_code == 200  # a new client: counted (2/2)
    assert _client(app, "10.0.0.3").get(f"/s/{t}/download").status_code == 410
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


def _client(app, ip):
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = ip
    return c


def test_download_manager_segments_with_limit_one(two_users, app):
    """max_downloads=1: a full GET then parallel Range segments, retries and HEAD from the same
    client all succeed and count once; another client is refused."""
    alice, _ = two_users
    data = b"0123456789" * 1000
    fid = upload(alice, "big.bin", data).json["id"]
    t = token(alice.post(f"/api/files/{fid}/shares", json={"max_downloads": 1}).json["share"])
    idm = _client(app, "192.168.1.50")
    assert idm.head(f"/s/{t}/download").status_code == 200  # a probe is not a download
    assert idm.get(f"/s/{t}/download").data == data
    for start in (0, 2500, 5000, 7500):
        r = idm.get(f"/s/{t}/download", headers={"Range": f"bytes={start}-{start + 2499}"})
        assert r.status_code == 206 and r.data == data[start:start + 2500]
    assert idm.get(f"/s/{t}/download", headers={"Range": "bytes=9000-"}).data == data[9000:]
    assert idm.head(f"/s/{t}/download").status_code == 200
    assert idm.get(f"/s/{t}").status_code == 200  # the page still opens for this client
    # a download manager on the same machine without the browser's cookies (IP fallback)
    assert _client(app, "192.168.1.50").get(f"/s/{t}/download", headers={"Range": "bytes=5000-"}).status_code == 206
    assert alice.get("/api/shares").json["shares"][0]["download_count"] == 1

    other = _client(app, "192.168.1.51")
    assert other.get(f"/s/{t}/download").status_code == 410
    assert other.get(f"/s/{t}/download", headers={"Range": "bytes=100-"}).status_code == 410
    assert other.head(f"/s/{t}/download").status_code == 410
    assert other.get(f"/s/{t}").status_code == 410


def test_first_request_may_be_a_range_and_is_counted(two_users, app):
    """No more free downloads through Range requests: without a grant any request counts."""
    alice, _ = two_users
    fid = upload(alice, "a.bin", b"x" * 100).json["id"]
    t = token(alice.post(f"/api/files/{fid}/shares", json={"max_downloads": 1}).json["share"])
    assert _client(app, "10.1.1.1").get(f"/s/{t}/download", headers={"Range": "bytes=50-"}).status_code == 206
    assert _client(app, "10.1.1.2").get(f"/s/{t}/download", headers={"Range": "bytes=50-"}).status_code == 410


def test_grant_expires(two_users, app, monkeypatch):
    alice, _ = two_users
    fid = upload(alice, "a.bin", b"x" * 100).json["id"]
    t = token(alice.post(f"/api/files/{fid}/shares", json={"max_downloads": 1}).json["share"])
    c = _client(app, "10.2.2.2")
    assert c.get(f"/s/{t}/download").status_code == 200
    import simplecloud.shares as shares_mod
    later = time.time() + app.config["SHARE_GRANT_SECONDS"] + 1
    monkeypatch.setattr(shares_mod.time, "time", lambda: later)
    assert c.get(f"/s/{t}/download", headers={"Range": "bytes=10-"}).status_code == 410


def test_grant_is_shared_across_workers(two_users, make_app, app):
    """Grants live in SQLite: a second app instance (another gunicorn worker) honours them."""
    alice, _ = two_users
    fid = upload(alice, "a.bin", b"x" * 100).json["id"]
    t = token(alice.post(f"/api/files/{fid}/shares", json={"max_downloads": 1}).json["share"])
    assert _client(app, "10.3.3.3").get(f"/s/{t}/download").status_code == 200
    worker2 = make_app()
    assert _client(worker2, "10.3.3.3").get(f"/s/{t}/download", headers={"Range": "bytes=10-"}).status_code == 206


def test_password_share_grant_cookie_vs_ip(two_users, app):
    alice, _ = two_users
    _, share = make_share(alice, password="pw", max_downloads=1)
    t = token(share)
    c = _client(app, "10.4.4.4")
    assert c.post(f"/s/{t}", json={"password": "pw"}).json["success"]
    assert c.get(f"/s/{t}/download").status_code == 200
    # the grant cookie works in a fresh session too (e.g. a download manager that copies cookies)
    cookie = c.get_cookie(f"share_grant_{share['id']}", path=f"/s/{t}")
    assert cookie is not None and cookie.http_only
    fresh = _client(app, "10.9.9.9")
    fresh.set_cookie(cookie.key, cookie.value, path=f"/s/{t}")
    assert fresh.get(f"/s/{t}/download", headers={"Range": "bytes=2-"}).status_code == 206
    # same IP without cookie or password: not counted again, but the password still applies
    assert _client(app, "10.4.4.4").get(f"/s/{t}/download").status_code == 403


def test_login_required_share(two_users, app):
    alice, bob = two_users
    _, share = make_share(alice, login_required=True)
    t = token(share)
    assert share["login_required"] is True
    anon = app.test_client()
    assert "/login" in anon.get(f"/s/{t}").get_data(as_text=True)
    r = anon.get(f"/s/{t}/download")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert anon.get(f"/s/{t}/view").status_code == 302
    assert bob.get(f"/s/{t}/download").data == b"%PDF-data"
    # Signing in from the share page comes back to it.
    from conftest import add_user, captcha_answer
    add_user(app, "carol")
    anon.get(r.headers["Location"])
    back = anon.post("/login", json={"username": "carol", "password": "pw-" + "x" * 8,
                                     "captcha": captcha_answer(app, anon)})
    assert back.json["redirect_url"] == f"/s/{t}"


def test_view_online_counts_as_download(two_users, app):
    alice, _ = two_users
    _, share = make_share(alice, max_downloads=1)
    t = token(share)
    viewer = _client(app, "10.0.0.1")
    assert 'id="share-view"' in viewer.get(f"/s/{t}").get_data(as_text=True)
    r = viewer.get(f"/s/{t}/view")
    assert r.data == b"%PDF-data" and r.headers["Content-Disposition"].startswith("inline")
    assert viewer.get(f"/s/{t}/view", headers={"Range": "bytes=2-"}).status_code == 206  # player range: not counted
    assert viewer.get(f"/s/{t}/download").status_code == 200  # same grant
    assert alice.get("/api/shares").json["shares"][0]["download_count"] == 1
    assert _client(app, "10.0.0.2").get(f"/s/{t}/view").status_code == 410  # no way around the limit
