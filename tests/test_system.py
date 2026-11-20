"""Admin "System / Storage" dashboard and "Back up now"."""
import os
import sqlite3
import time
from collections import namedtuple

import pytest

from conftest import add_user, login, upload
from simplecloud import system

MB = 1024 * 1024
Usage = namedtuple("Usage", "total used free")


@pytest.fixture
def dash(make_app, tmp_path, monkeypatch):
    """An app with admin `boss` (unlimited) and users alice/bob with quotas, on a fake 100 MB disk."""
    system.clear_cache()
    app = make_app(DB_BACKUP_FOLDER=str(tmp_path / "dbbackups"), STATS_CACHE_SECONDS=60)
    disk = {"value": Usage(100 * MB, 60 * MB, 40 * MB)}
    monkeypatch.setattr(system.shutil, "disk_usage", lambda _p: disk["value"])
    add_user(app, "boss")
    add_user(app, "alice")
    add_user(app, "bob")
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("UPDATE users SET is_admin = 1 WHERE username = 'boss'")
    conn.execute("UPDATE users SET quota_bytes = ? WHERE username = 'alice'", (80 * MB,))
    conn.execute("UPDATE users SET quota_bytes = ? WHERE username = 'bob'", (40 * MB,))
    conn.execute("DELETE FROM users WHERE username = 'admin'")  # the auto-created first admin
    conn.commit()
    conn.close()
    clients = {}
    for name in ("boss", "alice"):
        clients[name] = app.test_client()
        assert login(app, clients[name], name).json["success"]
    yield app, clients, disk
    system.clear_cache()


def test_storage_stats_and_over_allocation(dash):
    app, c, _disk = dash
    upload(c["alice"], "big.bin", b"a" * 3000)
    upload(c["alice"], "small.txt", b"a" * 10)
    r = c["boss"].get("/api/admin/system")
    assert r.status_code == 200
    d = r.json
    assert d["disk"] == {"total": 100 * MB, "used": 60 * MB, "free": 40 * MB, "cloud": 3010,
                         "other": 60 * MB - 3010, "free_pct": 40.0}
    users = {u["username"]: u for u in d["users"]}
    assert users["alice"]["used"] == 3010 and users["alice"]["quota"] == 80 * MB
    assert users["alice"]["pct_cloud"] == 100.0 and users["bob"]["pct_cloud"] == 0.0
    assert users["boss"]["quota"] is None and users["boss"]["pct_quota"] is None
    # 120 MB of quotas, but only 40 MB free + 3010 B already used can ever be filled
    alloc = d["allocation"]
    assert alloc["quota_total"] == 120 * MB and alloc["available"] == 40 * MB + 3010
    assert alloc["over_available"] and alloc["over_capacity"]  # also more than the whole 100 MB disk
    codes = {w["code"]: w for w in d["warnings"]}
    assert codes["over_allocated"]["allocated"] == 120 * MB
    assert codes["unlimited_users"]["users"] == ["boss"]
    assert "low_disk" not in codes
    # largest files: names, owners, sizes; nothing to download them with
    assert [f["name"] for f in d["largest"]] == ["big.bin", "small.txt"]
    assert d["largest"][0]["owner"] == "alice" and set(d["largest"][0]) == {"name", "size", "created_at", "owner"}


def test_no_over_allocation_warning_when_quotas_fit(dash):
    app, c, disk = dash
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("UPDATE users SET quota_bytes = ? WHERE username = 'alice'", (10 * MB,))
    conn.execute("UPDATE users SET quota_bytes = ? WHERE username IN ('boss', 'bob')", (10 * MB,))
    conn.commit()
    conn.close()
    codes = [w["code"] for w in c["boss"].get("/api/admin/system").json["warnings"]]
    assert "over_allocated" not in codes and "unlimited_users" not in codes


def test_low_disk_warning_and_cache(dash):
    app, c, disk = dash
    first = c["boss"].get("/api/admin/system").json
    disk["value"] = Usage(100 * MB, 95 * MB, 5 * MB)
    # cached for STATS_CACHE_SECONDS: the same figures come back
    assert c["boss"].get("/api/admin/system").json["disk"] == first["disk"]
    system.clear_cache()
    w = {x["code"]: x for x in c["boss"].get("/api/admin/system").json["warnings"]}
    assert w["low_disk"]["free_pct"] == 5.0


def test_backup_status(dash, tmp_path):
    app, c, _ = dash
    b = c["boss"].get("/api/admin/system").json["backups"]
    assert b["count"] == 0 and b["last"] is None and b["keep"] == 14 and b["writable"]
    assert {"code": "backup_missing"} in b["warnings"]
    folder = tmp_path / "dbbackups"
    folder.mkdir()
    old = folder / "simplecloud-db-20260101-030000.tar.gz"
    old.write_bytes(b"x" * 10)
    (folder / "simplecloud-20260101-030000.tar.gz").write_bytes(b"full")  # a full backup is not listed
    (folder / "notes.txt").write_text("not a backup")
    t = time.time() - 30 * 3600
    os.utime(old, (t, t))
    b = c["boss"].get("/api/admin/system").json["backups"]
    assert b["count"] == 1 and b["archives"][0]["name"] == old.name and b["archives"][0]["size"] == 10
    assert any(w["code"] == "backup_old" for w in b["warnings"])
    assert b["timer"] in (True, False, None)


def wait_for_run(client, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        run = client.get("/api/admin/backup").json["run"]
        if run["status"] != "running":
            return run
        time.sleep(0.05)
    raise AssertionError("backup did not finish")


def test_backup_now(dash, tmp_path):
    app, c, _ = dash
    r = c["boss"].post("/api/admin/backup")
    assert r.status_code == 202 and r.json["run"]["status"] in ("running", "done")
    run = wait_for_run(c["boss"])
    assert run["status"] == "done" and run["started_by"] == "boss", run
    assert os.path.basename(run["path"]).startswith("simplecloud-db-")
    assert os.path.dirname(run["path"]) == str(tmp_path / "dbbackups")
    b = c["boss"].get("/api/admin/system").json["backups"]
    assert b["count"] == 1 and b["age_hours"] < 1 and not any(w["code"].startswith("backup_m") for w in b["warnings"])


def test_backup_now_one_at_a_time_and_retention(dash, tmp_path):
    app, c, _ = dash
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("INSERT INTO backup_runs (status, started_at, started_by) VALUES ('running', ?, 'x')", (time.time(),))
    conn.commit()
    r = c["boss"].post("/api/admin/backup")
    assert r.status_code == 409 and r.json["run"]["started_by"] == "x"
    # a run left "running" by a dead worker stops blocking after RUN_STALE_SECONDS
    conn.execute("UPDATE backup_runs SET started_at = ?", (time.time() - system.RUN_STALE_SECONDS - 1,))
    conn.commit()
    conn.close()
    assert c["boss"].get("/api/admin/backup").json["run"]["status"] == "failed"
    app.config["BACKUP_KEEP"] = 2
    for _ in range(3):
        assert c["boss"].post("/api/admin/backup").status_code == 202
        assert wait_for_run(c["boss"])["status"] == "done"
        time.sleep(1.05)  # distinct timestamps
    assert len(list((tmp_path / "dbbackups").glob("simplecloud-db-*.tar.gz"))) == 2


def test_backup_folder_not_writable(dash, monkeypatch):
    app, c, _ = dash
    monkeypatch.setattr(system.os, "access", lambda *_a, **_k: False)
    b = c["boss"].get("/api/admin/system").json["backups"]
    assert not b["writable"] and any(w["code"] == "backup_not_writable" for w in b["warnings"])
    r = c["boss"].post("/api/admin/backup")
    assert r.status_code == 409 and r.json["folder"] == app.config["DB_BACKUP_FOLDER"]


def test_admin_only(dash):
    app, c, _ = dash
    for method, url in (("get", "/api/admin/system"), ("get", "/api/admin/backup"), ("post", "/api/admin/backup")):
        assert getattr(c["alice"], method)(url).status_code == 403, url
        assert getattr(app.test_client(), method)(url).status_code == 401, url
    conn = sqlite3.connect(app.config["DATABASE"])
    assert conn.execute("SELECT COUNT(*) FROM backup_runs").fetchone()[0] == 0
    conn.close()


def test_backup_now_requires_csrf(make_app, tmp_path):
    app = make_app(WTF_CSRF_ENABLED=True, DB_BACKUP_FOLDER=str(tmp_path / "b"))
    add_user(app, "boss")
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("UPDATE users SET is_admin = 1 WHERE username = 'boss'")
    conn.commit()
    conn.close()
    c = app.test_client()
    page = c.get("/login").get_data(as_text=True)
    import re
    token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
    from conftest import captcha_answer
    assert c.post("/login", headers={"X-CSRFToken": token}, json={
        "username": "boss", "password": "pw-xxxxxxxx", "captcha": captcha_answer(app, c)}).json["success"]
    assert c.post("/api/admin/backup").status_code == 400  # no token
    assert c.post("/api/admin/backup", headers={"X-CSRFToken": token}).status_code == 202
    wait_for_run(c)
