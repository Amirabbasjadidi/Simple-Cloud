import os
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

from simplecloud import create_app, limiter


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLECLOUD_ENV_FILE", str(tmp_path / "missing.env"))
    for var in ("SECRET_KEY", "DATABASE", "UPLOAD_FOLDER"):
        monkeypatch.delenv(var, raising=False)

    def _make(**overrides):
        cfg = {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE": str(tmp_path / "test.db"),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": False,
        }
        cfg.update(overrides)
        app = create_app(cfg)
        if app.config["RATELIMIT_ENABLED"]:
            limiter.reset()
        return app
    return _make


@pytest.fixture
def app(make_app):
    return make_app()


def add_user(app, username, password="pw-" + "x" * 8, **cols):
    conn = sqlite3.connect(app.config["DATABASE"])
    cur = conn.execute("INSERT INTO users (username, password) VALUES (?, ?)",
                       (username, generate_password_hash(password)))
    conn.commit()
    conn.close()
    return cur.lastrowid


def captcha_answer(app, client):
    with client.session_transaction() as s:
        token = s.get("captcha_token")
    conn = sqlite3.connect(app.config["DATABASE"])
    row = conn.execute("SELECT answer FROM captchas WHERE token = ?", (token,)).fetchone()
    conn.close()
    return row[0] if row else None


def login(app, client, username, password="pw-" + "x" * 8, captcha=...):
    client.get("/login")
    answer = captcha_answer(app, client) if captcha is ... else captcha
    return client.post("/login", json={"username": username, "password": password, "captcha": answer})


@pytest.fixture
def two_users(app):
    add_user(app, "alice")
    add_user(app, "bob")
    a, b = app.test_client(), app.test_client()
    assert login(app, a, "alice").json["success"]
    assert login(app, b, "bob").json["success"]
    return a, b


def upload(client, name, data=b"hello"):
    import io
    return client.post("/upload", data={"file": (io.BytesIO(data), name)}, content_type="multipart/form-data")


@pytest.fixture
def cert_dir():
    return os.path.join(os.path.dirname(__file__), "fixtures")
