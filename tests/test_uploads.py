import os
import sqlite3
import time

import pytest



def start(c, name="big.bin", size=10, folder=None):
    return c.post("/api/uploads", json={"name": name, "size": size, "folder_id": folder})


def put(c, uid, offset, data):
    return c.put(f"/api/uploads/{uid}?offset={offset}", data=data, content_type="application/octet-stream")


@pytest.fixture
def small_chunks(make_app):
    from conftest import add_user, login
    app = make_app(CHUNK_SIZE=4, MAX_FILE_SIZE=100)
    add_user(app, "alice")
    add_user(app, "bob")
    a, b = app.test_client(), app.test_client()
    login(app, a, "alice")
    login(app, b, "bob")
    return app, a, b


def test_chunked_upload_roundtrip_persian_name(small_chunks):
    app, a, _ = small_chunks
    data = "سلام دنیا!".encode()  # 19 bytes
    r = start(a, "گزارش.txt", len(data)).json
    uid = r["upload_id"]
    assert r["chunk_size"] == 4
    off = 0
    while off < len(data):
        resp = put(a, uid, off, data[off:off + 4]).json
        off = resp["received"]
    assert resp["file"]["name"] == "گزارش.txt"
    assert a.get(resp["file"]["download_url"]).data == data
    assert sqlite3.connect(app.config["DATABASE"]).execute("SELECT COUNT(*) FROM uploads").fetchone()[0] == 0
    assert not [n for n in os.listdir(os.path.join(app.config["UPLOAD_FOLDER"], "2")) if n.endswith(".part")]


def test_resume_after_interruption(small_chunks):
    _, a, _ = small_chunks
    uid = start(a, size=8).json["upload_id"]
    put(a, uid, 0, b"abcd")
    assert a.get(f"/api/uploads/{uid}").json["received"] == 4  # client asks where to continue
    r = put(a, uid, 0, b"abcd")  # stale offset
    assert r.status_code == 409 and r.json["received"] == 4
    assert put(a, uid, 4, b"efgh").json["file"]["size"] == 8


def test_chunk_checks_ownership(small_chunks):
    _, a, b = small_chunks
    uid = start(a, size=8).json["upload_id"]
    assert put(b, uid, 0, b"evil").status_code == 404
    assert b.get(f"/api/uploads/{uid}").status_code == 404
    assert b.delete(f"/api/uploads/{uid}").status_code == 404
    assert a.get(f"/api/uploads/{uid}").json["received"] == 0


def test_chunk_cannot_exceed_declared_size(small_chunks):
    _, a, _ = small_chunks
    uid = start(a, size=6).json["upload_id"]
    put(a, uid, 0, b"abcd")
    assert put(a, uid, 4, b"efgh").status_code == 400
    assert put(a, uid, 4, b"x" * 9).status_code == 413  # > 2 * CHUNK_SIZE
    assert put(a, uid, -1, b"ab").status_code == 400
    assert a.put(f"/api/uploads/{uid}?offset=x", data=b"ab").status_code == 400


def test_max_file_size(small_chunks):
    _, a, _ = small_chunks
    assert start(a, size=101).status_code == 413
    assert start(a, size=-5).status_code == 400
    assert a.post("/api/uploads", json={"name": "x", "size": "abc"}).status_code == 400


def test_quota_enforced_and_reserved(small_chunks):
    app, a, _ = small_chunks
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("UPDATE users SET quota_bytes = 10 WHERE username = 'alice'")
    conn.commit()
    assert start(a, size=11).status_code == 507
    uid = start(a, size=8).json["upload_id"]
    assert start(a, size=4).status_code == 507  # 8 already reserved
    assert a.get("/api/usage").json["reserved"] == 8
    # quota lowered while uploading: next chunk is refused
    conn.execute("UPDATE users SET quota_bytes = 5 WHERE username = 'alice'")
    conn.commit()
    assert put(a, uid, 0, b"abcd").status_code == 507


def test_cancel_removes_part(small_chunks):
    app, a, _ = small_chunks
    uid = start(a, size=8).json["upload_id"]
    put(a, uid, 0, b"abcd")
    assert a.delete(f"/api/uploads/{uid}").json["success"]
    assert os.listdir(os.path.join(app.config["UPLOAD_FOLDER"], "2")) == []
    assert a.get(f"/api/uploads/{uid}").status_code == 404


def test_abandoned_uploads_cleaned(small_chunks):
    app, a, _ = small_chunks
    uid = start(a, size=8).json["upload_id"]
    put(a, uid, 0, b"abcd")
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("UPDATE uploads SET updated_at = ?", (time.time() - app.config["UPLOAD_ABANDON_SECONDS"] - 1,))
    conn.commit()
    start(a, size=1)  # starting a new upload triggers cleanup
    assert a.get(f"/api/uploads/{uid}").status_code == 404
    parts = [n for n in os.listdir(os.path.join(app.config["UPLOAD_FOLDER"], "2")) if n.endswith(".part")]
    assert len(parts) == 1  # only the new one


def test_empty_file_completes_immediately(small_chunks):
    _, a, _ = small_chunks
    r = start(a, "empty.txt", 0).json
    assert r["file"]["size"] == 0


def test_upload_into_folder(small_chunks):
    _, a, b = small_chunks
    fid = a.post("/api/folders", json={"name": "F"}).json["folder"]["id"]
    assert start(b, size=1, folder=fid).status_code == 404
    uid = start(a, size=2, folder=fid).json["upload_id"]
    assert put(a, uid, 0, b"ab").json["file"]["folder_id"] == fid
