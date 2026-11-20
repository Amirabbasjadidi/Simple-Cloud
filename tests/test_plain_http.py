"""The supported deployment is plain HTTP on a LAN (http://cloud.local). Nothing may assume HTTPS."""
import re
import sqlite3

from conftest import add_user, captcha_answer, upload

BASE = "http://cloud.local"


def answer_for(app, client):
    """captcha_answer() for a client talking to cloud.local (the session cookie is per host)."""
    with client.session_transaction(base_url=BASE) as s:
        token = s.get("captcha_token")
    conn = sqlite3.connect(app.config["DATABASE"])
    row = conn.execute("SELECT answer FROM captchas WHERE token = ?", (token,)).fetchone()
    conn.close()
    return row[0]


def csrf_token(page):
    return re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)


def test_full_flow_over_http_with_csrf(make_app):
    app = make_app(WTF_CSRF_ENABLED=True)  # real CSRF, as in production
    add_user(app, "alice")
    c = app.test_client()
    page = c.get("/login", base_url=BASE)
    assert page.status_code == 200
    token = csrf_token(page.get_data(as_text=True))
    h = {"X-CSRFToken": token}

    # no CSRF token -> refused, even over HTTP
    assert c.post("/login", json={}, base_url=BASE).status_code == 400

    r = c.post("/login", base_url=BASE, headers=h, json={
        "username": "alice", "password": "pw-xxxxxxxx", "captcha": answer_for(app, c), "remember_me": True})
    assert r.json["success"], r.json
    for cookie in r.headers.getlist("Set-Cookie"):
        assert "Secure" not in cookie, cookie  # a Secure cookie would never be sent back over HTTP
        assert "HttpOnly" in cookie and "SameSite=Lax" in cookie

    # chunked upload
    up = c.post("/api/uploads", base_url=BASE, headers=h, json={"name": "report.txt", "size": 5})
    assert up.status_code == 200, up.json
    done = c.put(f"/api/uploads/{up.json['upload_id']}?offset=0", base_url=BASE, headers=h, data=b"hello",
                 content_type="application/octet-stream")
    fid = done.json["file"]["id"]
    assert c.get(f"/files/{fid}/download", base_url=BASE).data == b"hello"

    # share link: the URL uses the scheme the request came in with
    share = c.post(f"/api/files/{fid}/shares", base_url=BASE, headers=h, json={}).json["share"]
    assert share["url"].startswith("http://cloud.local/s/")
    anon = app.test_client()
    assert anon.get(share["url"]).data.decode().count("report.txt") >= 1
    assert anon.get(share["url"] + "/download").data == b"hello"

    # logout
    assert c.post("/logout", base_url=BASE, headers=h).json["success"]
    assert c.get("/files", base_url=BASE).status_code == 302


def test_no_https_assumptions(app):
    r = app.test_client().get("/login", base_url=BASE)
    assert r.status_code == 200  # no redirect to https
    assert "Strict-Transport-Security" not in r.headers
    assert "upgrade-insecure-requests" not in r.headers["Content-Security-Policy"]
    assert "block-all-mixed-content" not in r.headers["Content-Security-Policy"]
    assert "https://" not in r.get_data(as_text=True)
    assert not app.config["SESSION_COOKIE_SECURE"] and not app.config["REMEMBER_COOKIE_SECURE"]


def test_behind_nginx_over_http(make_app):
    """With PROXY_COUNT=1 the forwarded scheme (http) and host are used for generated links."""
    app = make_app(PROXY_COUNT=1)
    add_user(app, "alice")
    c = app.test_client()
    fwd = {"X-Forwarded-For": "10.0.0.5", "X-Forwarded-Proto": "http", "X-Forwarded-Host": "cloud.local"}
    c.get("/login", headers=fwd)
    r = c.post("/login", headers=fwd, json={"username": "alice", "password": "pw-xxxxxxxx", "captcha": captcha_answer(app, c)})
    assert r.json["success"]
    fid = upload(c, "a.txt").json["id"]
    url = c.post(f"/api/files/{fid}/shares", headers=fwd, json={}).json["share"]["url"]
    assert url.startswith("http://cloud.local/s/")
