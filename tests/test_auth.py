import sqlite3

import pytest

from conftest import add_user, captcha_answer, login
from simplecloud import create_app


def test_refuses_to_start_without_secret_key(make_app):
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        make_app(SECRET_KEY=None)


def test_first_run_creates_random_admin_once(make_app, capsys):
    app = make_app()
    out = capsys.readouterr().out
    assert "Username: admin" in out and "1234" not in out
    conn = sqlite3.connect(app.config["DATABASE"])
    rows = conn.execute("SELECT username, must_change_password FROM users").fetchall()
    assert rows == [("admin", 1)]
    make_app()  # second start: no new admin, nothing printed
    assert "Initial admin" not in capsys.readouterr().out
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_legacy_root_default_password_is_flagged(make_app):
    app = make_app()
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("DELETE FROM users")
    conn.commit()
    add_user(app, "root", "1234")
    make_app()
    assert conn.execute("SELECT must_change_password FROM users WHERE username='root'").fetchone()[0] == 1


def test_captcha_answer_not_in_session_cookie(app):
    c = app.test_client()
    c.get("/login")
    with c.session_transaction() as s:
        # only an opaque token (and the CSRF token) - no answer
        assert set(s.keys()) <= {"captcha_token", "csrf_token"} and "captcha_token" in s
        assert captcha_answer(app, c) is not None


def test_login_ok(app):
    add_user(app, "alice")
    c = app.test_client()
    r = login(app, c, "alice")
    assert r.status_code == 200 and r.json["success"]
    assert c.get("/upload").status_code == 200


@pytest.mark.parametrize("bad", [None, "", "abc", "1.5", [], {"x": 1}])
def test_bad_captcha_input_no_500(app, bad):
    add_user(app, "alice")
    c = app.test_client()
    r = login(app, c, "alice", captcha=bad)
    assert r.status_code == 400 and not r.json["success"]
    assert r.json["captcha_image"]  # client gets a fresh captcha


def test_non_json_body_no_500(app):
    c = app.test_client()
    c.get("/login")
    assert c.post("/login", data="garbage").status_code == 400


def test_captcha_is_single_use(app):
    add_user(app, "alice")
    c = app.test_client()
    c.get("/login")
    answer = captcha_answer(app, c)
    with c.session_transaction() as s:
        token = s["captcha_token"]
    # wrong password burns the captcha
    assert c.post("/login", json={"username": "alice", "password": "wrong", "captcha": answer}).status_code == 401
    # replaying the old token + old answer fails even with the right password
    with c.session_transaction() as s:
        s["captcha_token"] = token
    r = c.post("/login", json={"username": "alice", "password": "pw-xxxxxxxx", "captcha": answer})
    assert r.status_code == 400


def test_captcha_invalidated_on_success(app):
    add_user(app, "alice")
    c = app.test_client()
    c.get("/login")
    with c.session_transaction() as s:
        token = s["captcha_token"]
    assert login(app, c, "alice").json["success"]
    conn = sqlite3.connect(app.config["DATABASE"])
    assert conn.execute("SELECT COUNT(*) FROM captchas WHERE token=?", (token,)).fetchone()[0] == 0


def test_lockout_per_username_shared_in_db(make_app):
    app = make_app(LOGIN_MAX_FAILURES_USER=3, LOGIN_MAX_FAILURES_IP=100)
    add_user(app, "alice")
    c = app.test_client()
    for _ in range(3):
        assert login(app, c, "alice", "wrong").status_code == 401
    # a second "worker" (separate app on the same DB) sees the same lockout, even with the right password
    other = make_app(LOGIN_MAX_FAILURES_USER=3, LOGIN_MAX_FAILURES_IP=100)
    r = login(other, other.test_client(), "alice")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    # other usernames from the same IP are unaffected
    add_user(app, "bob")
    assert login(app, c, "bob").json["success"]


def test_lockout_per_ip(make_app):
    app = make_app(LOGIN_MAX_FAILURES_USER=100, LOGIN_MAX_FAILURES_IP=4)
    add_user(app, "alice")
    c = app.test_client()
    for i in range(4):
        login(app, c, f"nobody{i}", "wrong")
    assert login(app, c, "alice").status_code == 429


def test_lockout_expires(make_app, monkeypatch):
    import time
    app = make_app(LOGIN_MAX_FAILURES_USER=2, LOGIN_LOCKOUT_SECONDS=60)
    add_user(app, "alice")
    c = app.test_client()
    for _ in range(2):
        login(app, c, "alice", "wrong")
    assert login(app, c, "alice").status_code == 429
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 61)
    assert login(app, c, "alice").json["success"]


def test_flask_limiter_on_login(make_app):
    app = make_app(RATELIMIT_ENABLED=True)
    c = app.test_client()
    codes = [c.post("/login", json={}).status_code for _ in range(31)]
    assert codes[-1] == 429


def test_csrf_required(make_app):
    app = make_app(WTF_CSRF_ENABLED=True)
    add_user(app, "alice")
    c = app.test_client()
    c.get("/login")
    r = c.post("/login", json={"username": "alice", "password": "pw-xxxxxxxx", "captcha": captcha_answer(app, c)})
    assert r.status_code == 400 and "token" in r.json["error"].lower()


def test_logout_is_post_only(app):
    add_user(app, "alice")
    c = app.test_client()
    login(app, c, "alice")
    assert c.get("/logout").status_code == 405
    assert c.post("/logout").json["success"]
    assert c.get("/upload").status_code == 302


def test_cookie_flags(make_app):
    app = make_app(SESSION_COOKIE_SECURE=True)
    add_user(app, "alice")
    c = app.test_client()
    c.get("/login")
    r = c.post("/login", json={"username": "alice", "password": "pw-xxxxxxxx",
                               "captcha": captcha_answer(app, c), "remember_me": True})
    cookies = r.headers.getlist("Set-Cookie")
    session_cookie = next(x for x in cookies if x.startswith("session="))
    assert "HttpOnly" in session_cookie and "SameSite=Lax" in session_cookie and "Secure" in session_cookie
    remember = next(x for x in cookies if x.startswith("remember_token="))
    assert "HttpOnly" in remember and "SameSite=Lax" in remember


def test_security_headers(app):
    r = app.test_client().get("/login")
    assert "frame-ancestors" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"]


def test_forced_password_change_flow(make_app, capsys):
    import re
    app = make_app()
    pw = re.search(r"Password: (\S+)", capsys.readouterr().out).group(1)
    c = app.test_client()
    r = login(app, c, "admin", pw)
    assert r.json["redirect_url"] == "/account"
    assert c.get("/files").headers["Location"].endswith("/account")
    assert c.get("/api/files").status_code == 403
    assert c.post("/api/account/password", json={"current_password": pw, "new_password": "short"}).status_code == 400
    assert c.post("/api/account/password", json={"current_password": "nope", "new_password": "long-enough-1"}).status_code == 400
    r = c.post("/api/account/password", json={"current_password": pw, "new_password": "long-enough-1"})
    assert r.json["success"]
    assert c.get("/files").status_code == 200  # same session continues


def test_password_change_signs_out_other_sessions(app):
    add_user(app, "alice")
    a, b = app.test_client(), app.test_client()
    login(app, a, "alice")
    login(app, b, "alice")
    assert a.post("/api/account/password", json={"current_password": "pw-xxxxxxxx",
                                                  "new_password": "new-password-1"}).json["success"]
    assert a.get("/files").status_code == 200
    assert b.get("/files").status_code == 302


def test_disabled_user_cannot_login_and_is_signed_out(app):
    add_user(app, "alice")
    c = app.test_client()
    login(app, c, "alice")
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("UPDATE users SET disabled = 1, session_version = session_version + 1 WHERE username='alice'")
    conn.commit()
    assert c.get("/files").status_code == 302
    assert login(app, app.test_client(), "alice").status_code == 401


def test_forced_password_change_page_does_not_loop(app):
    """Regression: /account's own requests must not be refused with must_change_password
    (app.js would then redirect to /account again, forever)."""
    import re
    from conftest import add_user, login
    import sqlite3
    add_user(app, "alice")
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("UPDATE users SET must_change_password = 1 WHERE username = 'alice'")
    conn.commit()
    conn.close()
    c = app.test_client()
    r = login(app, c, "alice")
    assert r.json["redirect_url"].endswith("/account")
    page = c.get("/account")
    assert page.status_code == 200
    # every same-origin API the page's scripts call on load must answer normally
    assert re.search(r"\bdata-usage\b", page.get_data(as_text=True))
    usage = c.get("/api/usage")
    assert usage.status_code == 200 and "must_change_password" not in usage.json
    # everything else stays blocked until the password is changed
    assert c.get("/files").status_code == 302
    assert c.get("/api/files").json["must_change_password"]
    # the client never redirects when it is already on /account
    assert 'window.location.pathname !== "/account"' in c.get("/static/app.js").get_data(as_text=True)

    assert c.post("/api/account/password", json={"current_password": "pw-xxxxxxxx",
                                                 "new_password": "new-pass-123"}).json["success"]
    assert c.get("/files").status_code == 200
    assert c.get("/api/files").status_code == 200
