import sqlite3
import time

from conftest import add_user, captcha_answer
from simplecloud import totp


def test_rfc6238_vector():
    # RFC 6238 appendix B, SHA-1 (8 digits)
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32("12345678901234567890")
    assert totp.code_at(secret, 59 // 30, digits=8) == "94287082"
    assert totp.code_at(secret, 1111111109 // 30, digits=8) == "07081804"


def test_verify_window_and_replay():
    s = totp.new_secret()
    now = 1_700_000_000
    step = now // 30
    assert totp.verify(s, totp.code_at(s, step), now=now) == step
    assert totp.verify(s, totp.code_at(s, step - 1), now=now) == step - 1
    assert totp.verify(s, totp.code_at(s, step - 2), now=now) is None
    assert totp.verify(s, totp.code_at(s, step), last_step=step, now=now) is None
    assert totp.verify(s, "abc", now=now) is None and totp.verify(s, None, now=now) is None


def enable_2fa(client):
    r = client.post("/api/account/totp/setup").json
    assert r["uri"].startswith("otpauth://totp/") and "secret=" in r["uri"]
    secret = r["secret"]
    assert client.post("/api/account/totp/enable", json={"code": "000000"}).status_code == 400
    step = int(time.time() // 30)
    assert client.post("/api/account/totp/enable", json={"code": totp.code_at(secret, step)}).json["success"]
    return secret


def password_step(app, client, user="alice", password="pw-xxxxxxxx"):
    client.get("/login")
    return client.post("/login", json={"username": user, "password": password, "captcha": captcha_answer(app, client)})


def test_login_requires_code(app):
    add_user(app, "alice")
    c = app.test_client()
    assert password_step(app, c).json["success"]
    secret = enable_2fa(c)

    c2 = app.test_client()
    r = password_step(app, c2)
    assert r.json["totp_required"] and "redirect_url" not in r.json
    assert c2.get("/files").status_code == 302  # not logged in yet
    assert c2.post("/login/totp", json={"code": "123456"}).status_code == 401
    # the code used to enable is burned (replay protection); use the next step
    code = totp.code_at(secret, int(time.time() // 30) + 1)
    assert c2.post("/login/totp", json={"code": code}).json["success"]
    assert c2.get("/files").status_code == 200
    # the same code in another session is rejected
    c3 = app.test_client()
    password_step(app, c3)
    assert c3.post("/login/totp", json={"code": code}).status_code == 401


def test_totp_without_password_step(app):
    assert app.test_client().post("/login/totp", json={"code": "123456"}).status_code == 400


def test_totp_guessing_locks_out(make_app):
    app = make_app(LOGIN_MAX_FAILURES_USER=3, LOGIN_MAX_FAILURES_IP=100)
    add_user(app, "alice")
    c = app.test_client()
    password_step(app, c)
    enable_2fa(c)
    c2 = app.test_client()
    password_step(app, c2)
    for _ in range(3):
        assert c2.post("/login/totp", json={"code": "000000"}).status_code == 401
    assert c2.post("/login/totp", json={"code": "000000"}).status_code == 429
    # re-entering the password does not reset the counter
    assert password_step(app, app.test_client()).status_code == 429


def test_disable_requires_password_and_code(app):
    add_user(app, "alice")
    c = app.test_client()
    password_step(app, c)
    secret = enable_2fa(c)
    code = totp.code_at(secret, int(time.time() // 30) + 1)
    assert c.post("/api/account/totp/disable", json={"password": "wrong", "code": code}).status_code == 400
    assert c.post("/api/account/totp/disable", json={"password": "pw-xxxxxxxx", "code": code}).json["success"]
    assert password_step(app, app.test_client()).json.get("redirect_url")


def test_cli_can_reset_2fa(app):
    add_user(app, "alice")
    c = app.test_client()
    password_step(app, c)
    enable_2fa(c)
    conn = sqlite3.connect(app.config["DATABASE"])
    assert conn.execute("SELECT totp_secret IS NOT NULL FROM users WHERE username='alice'").fetchone()[0]
    assert app.test_cli_runner().invoke(args=["disable2fa", "--username", "alice"]).exit_code == 0
    assert password_step(app, app.test_client()).json.get("redirect_url")


def test_account_page_shows_setup(app):
    add_user(app, "alice")
    c = app.test_client()
    password_step(app, c)
    assert b'id="totp-start"' in c.get("/account").data
