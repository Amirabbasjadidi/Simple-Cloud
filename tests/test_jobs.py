import os
import sqlite3
import time

import pytest

from simplecloud import jobs
from test_ssrf import Handler, _serve, fake_dns  # noqa: F401  (fixture reused)


@pytest.fixture
def srv():
    s = _serve()
    yield s
    s.shutdown()


def wait(client, job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/fetch/{job_id}").json["job"]
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_fetch_job_success_into_folder(two_users, srv):
    alice, bob = two_users
    port = srv.server_address[1]
    folder = alice.post("/api/folders", json={"name": "Web"}).json["folder"]["id"]
    r = alice.post("/api/fetch", json={"urls": [f"http://public.test:{port}/cd"], "folder_id": folder})
    job = wait(alice, r.json["jobs"][0]["id"])
    assert job["status"] == "done" and job["name"] == "گزارش.pdf" and job["done_bytes"] == 5
    files = alice.get("/api/files", query_string={"folder": folder}).json["files"]
    assert [f["name"] for f in files] == ["گزارش.pdf"]
    # isolation: bob can't see or cancel alice's job
    assert bob.get(f"/api/fetch/{job['id']}").status_code == 404
    assert bob.delete(f"/api/fetch/{job['id']}").status_code == 404
    assert bob.get("/api/fetch").json["jobs"] == []


def test_fetch_job_failure_reported(two_users, srv):
    alice, _ = two_users
    port = srv.server_address[1]
    job = alice.post("/api/fetch", json={"url": f"http://public.test:{port}/redir/http__10.0.0.1/"}).json["jobs"][0]
    job = wait(alice, job["id"])
    assert job["status"] == "failed" and "not allowed" in job["error"]
    assert alice.get("/api/files").json["files"] == []


def test_fetch_validation_and_limits(make_app, srv):
    from conftest import add_user, login
    app = make_app(FETCH_MAX_ACTIVE_JOBS=2)
    add_user(app, "alice")
    c = app.test_client()
    login(app, c, "alice")
    assert c.post("/api/fetch", json={}).status_code == 400
    assert c.post("/api/fetch", json={"url": "ftp://x/y"}).status_code == 400
    assert c.post("/api/fetch", json={"urls": ["http://a.test/1", "http://a.test/2", "http://a.test/3"]}).status_code == 429


def test_fetch_respects_quota(two_users, app, srv):
    alice, _ = two_users
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("UPDATE users SET quota_bytes = 3 WHERE username = 'alice'")
    conn.commit()
    port = srv.server_address[1]
    job = alice.post("/api/fetch", json={"url": f"http://public.test:{port}/a.txt"}).json["jobs"][0]
    job = wait(alice, job["id"])
    assert job["status"] == "failed" and "quota" in job["error"]


def test_stale_jobs_marked_failed_and_temp_removed(app, two_users):
    alice, _ = two_users
    part = os.path.join(app.config["UPLOAD_FOLDER"], "2", "deadbeef")
    os.makedirs(os.path.dirname(part), exist_ok=True)
    open(part, "wb").write(b"partial")
    conn = sqlite3.connect(app.config["DATABASE"])
    old = time.time() - jobs.STALE_AFTER - 5
    conn.execute("INSERT INTO jobs (id, user_id, url, status, stored_name, worker, heartbeat, created_at, updated_at) "
                 "VALUES ('j1', 2, 'http://x.test/', 'running', '2/deadbeef', 'host:1:dead', ?, ?, ?)", (old, old, old))
    conn.execute("INSERT INTO jobs (id, user_id, url, status, worker, heartbeat, created_at, updated_at) "
                 "VALUES ('j2', 2, 'http://x.test/', 'running', 'host:2:alive', ?, ?, ?)",
                 (time.time(), time.time(), time.time()))
    conn.commit()
    listing = {j["id"]: j for j in alice.get("/api/fetch").json["jobs"]}
    assert listing["j1"]["status"] == "failed" and "restarted" in listing["j1"]["error"]
    assert listing["j2"]["status"] == "running"  # fresh heartbeat: another live worker
    assert not os.path.exists(part)


def test_stale_jobs_failed_on_startup(make_app):
    app = make_app()
    conn = sqlite3.connect(app.config["DATABASE"])
    old = time.time() - 3600
    conn.execute("INSERT INTO jobs (id, user_id, url, status, heartbeat, created_at, updated_at) "
                 "VALUES ('j1', 1, 'http://x.test/', 'queued', ?, ?, ?)", (old, old, old))
    conn.commit()
    make_app()  # a new worker starts
    assert conn.execute("SELECT status FROM jobs WHERE id = 'j1'").fetchone()[0] == "failed"


def test_dismiss_finished_job(two_users, srv):
    alice, _ = two_users
    port = srv.server_address[1]
    job = wait(alice, alice.post("/api/fetch", json={"url": f"http://public.test:{port}/a.txt"}).json["jobs"][0]["id"])
    assert alice.delete(f"/api/fetch/{job['id']}").json["success"]
    assert alice.get("/api/fetch").json["jobs"] == []


def test_admin_url_limit_and_exemption(two_users, app, srv):
    alice, bob = two_users
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("INSERT INTO settings (key, value) VALUES ('fetch_limit_bytes', '1000')")
    conn.execute("UPDATE users SET fetch_unlimited = 1 WHERE username = 'bob'")
    conn.commit()
    url = f"http://public.test:{srv.server_address[1]}/big"  # 5000 bytes
    job = wait(alice, alice.post("/api/fetch", json={"url": url}).json["jobs"][0]["id"])
    assert job["status"] == "failed" and "larger" in job["error"]
    job = wait(bob, bob.post("/api/fetch", json={"url": url}).json["jobs"][0]["id"])
    assert job["status"] == "done"
