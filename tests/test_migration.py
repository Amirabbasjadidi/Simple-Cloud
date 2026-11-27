import os
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

from simplecloud import migrate
from simplecloud.db import LATEST_VERSION

LEGACY_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL);
CREATE TABLE files (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, path TEXT NOT NULL,
                    uploaded_by TEXT NOT NULL);
"""


@pytest.fixture
def legacy(tmp_path):
    """An install of the original app: users root/alice, 3 files, 1 orphan row, 1 missing file, 1 stray file."""
    db = tmp_path / "database.db"
    up = tmp_path / "uploads"
    up.mkdir()
    conn = sqlite3.connect(db)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute("INSERT INTO users (username, password) VALUES ('root', ?)", (generate_password_hash("1234"),))
    conn.execute("INSERT INTO users (username, password) VALUES ('alice', ?)", (generate_password_hash("pw"),))
    for name, owner, data in [("report.pdf", "alice", b"PDF"), ("photo.png", "root", b"PNG"),
                              ("notes (1).txt", "alice", b"notes"), ("ghost.txt", "deleted_user", b"ghost")]:
        (up / name).write_bytes(data)
        conn.execute("INSERT INTO files (name, path, uploaded_by) VALUES (?, ?, ?)", (name, f"uploads/{name}", owner))
    conn.execute("INSERT INTO files (name, path, uploaded_by) VALUES ('gone.txt', 'uploads/gone.txt', 'alice')")
    (up / "stray.bin").write_bytes(b"stray")
    conn.commit()
    conn.close()
    cfg = {"DATABASE": str(db), "UPLOAD_FOLDER": str(up), "BACKUP_FOLDER": str(tmp_path / "backups")}
    return cfg


def snapshot(cfg):
    files = {}
    for d, _, names in os.walk(cfg["UPLOAD_FOLDER"]):
        for n in names:
            p = os.path.join(d, n)
            files[os.path.relpath(p, cfg["UPLOAD_FOLDER"])] = open(p, "rb").read()
    conn = sqlite3.connect(cfg["DATABASE"])
    dump = "\n".join(conn.iterdump())
    conn.close()
    return files, dump


def test_dry_run_changes_nothing(legacy):
    before = snapshot(legacy)
    out = []
    migrate.run(legacy, dry_run=True, echo=out.append)
    assert snapshot(legacy) == before
    assert not os.path.exists(legacy["BACKUP_FOLDER"])
    text = "\n".join(out)
    assert "report.pdf" in text and "owner no longer exists" in text and "file not found" in text
    assert "stray.bin" in text


def test_migration_moves_files_without_data_loss(legacy):
    out = []
    migrate.run(legacy, echo=out.append)
    conn = sqlite3.connect(legacy["DATABASE"])
    conn.row_factory = sqlite3.Row
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
    rows = {r["name"]: r for r in conn.execute(
        "SELECT f.*, u.username FROM files f JOIN users u ON u.id = f.user_id")}
    assert set(rows) == {"report.pdf", "photo.png", "notes (1).txt"}
    assert rows["report.pdf"]["username"] == "alice" and rows["photo.png"]["username"] == "root"
    assert rows["report.pdf"]["mime_type"] == "application/pdf" and rows["report.pdf"]["size"] == 3
    for r in rows.values():
        path = os.path.join(legacy["UPLOAD_FOLDER"], r["stored_name"])
        assert r["stored_name"].startswith(f"{r['user_id']}/")
        assert os.path.isfile(path)
    assert open(os.path.join(legacy["UPLOAD_FOLDER"], rows["notes (1).txt"]["stored_name"]), "rb").read() == b"notes"
    # ids preserved
    legacy_ids = {r["name"]: r["id"] for r in conn.execute("SELECT * FROM legacy_files")}
    assert all(rows[n]["id"] == legacy_ids[n] for n in rows)
    # orphan + stray untouched, legacy table kept
    assert open(os.path.join(legacy["UPLOAD_FOLDER"], "ghost.txt"), "rb").read() == b"ghost"
    assert os.path.isfile(os.path.join(legacy["UPLOAD_FOLDER"], "stray.bin"))
    assert conn.execute("SELECT COUNT(*) FROM legacy_files").fetchone()[0] == 5
    # legacy root/1234 must change password
    assert conn.execute("SELECT must_change_password FROM users WHERE username='root'").fetchone()[0] == 1
    # backup holds the original DB and files
    (stamp,) = os.listdir(legacy["BACKUP_FOLDER"])
    bdir = os.path.join(legacy["BACKUP_FOLDER"], stamp)
    assert open(os.path.join(bdir, "uploads", "report.pdf"), "rb").read() == b"PDF"
    b = sqlite3.connect(os.path.join(bdir, "database.db"))
    assert b.execute("SELECT COUNT(*) FROM files WHERE uploaded_by='alice'").fetchone()[0] == 3


def test_migration_is_idempotent(legacy):
    migrate.run(legacy, echo=lambda *_: None)
    after = snapshot(legacy)
    out = []
    migrate.run(legacy, echo=out.append)
    assert "up to date" in out[0]
    assert snapshot(legacy) == after


def test_migration_resumes_after_crash(legacy, monkeypatch):
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt("simulated crash")
        real_replace(src, dst)

    monkeypatch.setattr(migrate.os, "replace", flaky)
    with pytest.raises(KeyboardInterrupt):
        migrate.run(legacy, echo=lambda *_: None)
    conn = sqlite3.connect(legacy["DATABASE"])
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0  # not finished
    monkeypatch.setattr(migrate.os, "replace", real_replace)

    out = []
    migrate.run(legacy, echo=out.append)
    assert any("Resuming" in line for line in out)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
    names = sorted(r[0] for r in conn.execute("SELECT name FROM files"))
    assert names == ["notes (1).txt", "photo.png", "report.pdf"]
    assert len(os.listdir(legacy["BACKUP_FOLDER"])) == 2  # one backup per run


def test_resume_when_crash_after_move_before_commit(legacy, monkeypatch):
    """File already renamed on disk but its row not committed: the re-run must not lose it."""
    state = {"armed": True}

    class Boom(Exception):
        pass

    orig_replace = os.replace

    def replace_then_crash(src, dst):
        orig_replace(src, dst)
        if state["armed"]:
            state["armed"] = False
            raise Boom()

    monkeypatch.setattr(migrate.os, "replace", replace_then_crash)
    with pytest.raises(Boom):
        migrate.run(legacy, echo=lambda *_: None)
    monkeypatch.setattr(migrate.os, "replace", orig_replace)
    migrate.run(legacy, echo=lambda *_: None)
    conn = sqlite3.connect(legacy["DATABASE"])
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 3
    for (stored,) in conn.execute("SELECT stored_name FROM files"):
        assert os.path.isfile(os.path.join(legacy["UPLOAD_FOLDER"], stored))


def _shape(db_path):
    conn = sqlite3.connect(db_path)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "AND name NOT IN ('legacy_files') ORDER BY name")]
    shape = {}
    for t in tables:
        shape[t] = (
            sorted((c[1], c[2].upper(), c[3], c[5]) for c in conn.execute(f"PRAGMA table_info({t})")),
            sorted(tuple(fk[2:5]) for fk in conn.execute(f"PRAGMA foreign_key_list({t})")),
            sorted(i[1] for i in conn.execute(f"PRAGMA index_list({t})") if not i[1].startswith("sqlite_")),
        )
    return shape


def test_fresh_schema_matches_migrated_schema(legacy, make_app, tmp_path):
    migrate.run(legacy, echo=lambda *_: None)
    app = make_app(DATABASE=str(tmp_path / "fresh.db"))
    assert _shape(legacy["DATABASE"]) == _shape(app.config["DATABASE"])


def test_app_refuses_requests_until_migrated(legacy, make_app):
    app = make_app(DATABASE=legacy["DATABASE"], UPLOAD_FOLDER=legacy["UPLOAD_FOLDER"],
                   BACKUP_FOLDER=legacy["BACKUP_FOLDER"])
    assert app.test_client().get("/login").status_code == 503
    migrate.run(app.config, echo=lambda *_: None)
    assert app.test_client().get("/login").status_code == 200


def test_v6_keeps_share_links_and_grants(legacy, monkeypatch):
    """The shares table is rebuilt in v6: links, counters and download grants must survive."""
    monkeypatch.setattr(migrate, "LATEST_VERSION", 5)
    migrate.run(legacy, echo=lambda *_: None)
    conn = sqlite3.connect(legacy["DATABASE"])
    fid = conn.execute("SELECT id FROM files LIMIT 1").fetchone()[0]
    uid = conn.execute("SELECT user_id FROM files WHERE id = ?", (fid,)).fetchone()[0]
    conn.execute("INSERT INTO shares (token, user_id, file_id, created_at, max_downloads, download_count, "
                 "login_required) VALUES ('tok', ?, ?, 1, 3, 2, 1)", (uid, fid))
    conn.execute("INSERT INTO share_grants (share_id, token_hash, ip, expires_at) VALUES (1, 'h', '1.2.3.4', 9e9)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(migrate, "LATEST_VERSION", LATEST_VERSION)
    migrate.run(legacy, echo=lambda *_: None)
    conn = sqlite3.connect(legacy["DATABASE"])
    conn.row_factory = sqlite3.Row
    s = conn.execute("SELECT * FROM shares WHERE token = 'tok'").fetchone()
    assert (s["file_id"], s["folder_id"], s["max_downloads"], s["download_count"], s["login_required"]) == (fid, None, 3, 2, 1)
    assert conn.execute("SELECT COUNT(*) FROM share_grants").fetchone()[0] == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
