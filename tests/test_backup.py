import json
import os
import sqlite3
import tarfile

from conftest import add_user, login, upload


def run_backup(app, *args):
    r = app.test_cli_runner().invoke(args=["backup", *args])
    assert r.exit_code == 0, r.output
    return r.output


def archives(folder):
    return sorted(f for f in os.listdir(folder) if f.endswith(".tar.gz"))


def test_backup_and_restore_round_trip(make_app, tmp_path):
    app = make_app(BACKUP_FOLDER=str(tmp_path / "backups"))
    add_user(app, "alice")
    c = app.test_client()
    assert login(app, c, "alice").json["success"]
    fid = upload(c, "گزارش.txt", b"precious bytes").json["id"]
    # an in-progress chunked upload must not end up in the archive
    c.post("/api/uploads", json={"name": "big.bin", "size": 100})

    out = run_backup(app)
    [name] = archives(tmp_path / "backups")
    assert name in out and oct(os.stat(tmp_path / "backups" / name).st_mode & 0o777) in ("0o600", "0o666")
    with tarfile.open(tmp_path / "backups" / name) as tar:
        names = tar.getnames()
        assert "database.db" in names and not any(n.endswith(".part") for n in names)
        manifest = json.load(tar.extractfile("manifest.json"))
        assert manifest["files"] == 1 and manifest["missing"] == 0
        restore = tmp_path / "restored"
        tar.extractall(restore, filter="data")

    # restore = point a fresh app at the extracted database and uploads
    conn = sqlite3.connect(restore / "database.db")
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
    app2 = make_app(DATABASE=str(restore / "database.db"), UPLOAD_FOLDER=str(restore / "uploads"))
    c2 = app2.test_client()
    assert login(app2, c2, "alice").json["success"]
    assert c2.get(f"/files/{fid}/download").data == b"precious bytes"


def test_retention_and_output_dir(app, tmp_path):
    out = tmp_path / "elsewhere"
    for _ in range(4):
        run_backup(app, "--output-dir", str(out), "--keep", "2")
    assert len(archives(out)) == 2
    assert not [f for f in os.listdir(out) if f.endswith(".part")]
    # unrelated files in the folder are never touched by retention
    (out / "notes.txt").write_text("keep me")
    run_backup(app, "--output-dir", str(out), "--keep", "1")
    assert (out / "notes.txt").exists() and len(archives(out)) == 1


def test_backup_works_while_db_is_open(app, tmp_path):
    """The online backup API copies a consistent snapshot even with an open connection mid-transaction."""
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("BEGIN")
    conn.execute("INSERT INTO users (username, password) VALUES ('uncommitted', 'x')")
    try:
        run_backup(app, "--output-dir", str(tmp_path / "b"))
    finally:
        conn.rollback()
        conn.close()
    [name] = archives(tmp_path / "b")
    with tarfile.open(tmp_path / "b" / name) as tar:
        tar.extract("database.db", tmp_path / "x", filter="data")
    snap = sqlite3.connect(tmp_path / "x" / "database.db")
    assert snap.execute("SELECT COUNT(*) FROM users WHERE username='uncommitted'").fetchone()[0] == 0
    snap.close()
