import os
from urllib.parse import quote

import pytest

from conftest import upload
from simplecloud.storage import clean_name


@pytest.mark.parametrize("raw,expected", [
    ("گزارش.pdf", "گزارش.pdf"),
    ("می‌خواهم.txt", "می‌خواهم.txt"),  # ZWNJ kept
    ("../../etc/passwd", "passwd"),
    ("C:\\Users\\x\\a.txt", "a.txt"),
    ("evil\u202egnp.exe", "evilgnp.exe"),  # bidi override stripped
    ("a\x00b\nc.txt", "abc.txt"),
    ("..", "file"), ("", "file"), ("   ", "file"),
    ("日本語 ファイル.png", "日本語 ファイル.png"),
])
def test_clean_name(raw, expected):
    assert clean_name(raw) == expected


def test_clean_name_length_keeps_extension():
    name = clean_name("ا" * 400 + ".pdf")
    assert len(name) == 255 and name.endswith(".pdf")


def test_persian_upload_roundtrip(two_users, app):
    alice, _ = two_users
    r = upload(alice, "گزارش.pdf", b"%PDF-1.4")
    assert r.json["filename"] == "گزارش.pdf"
    f = alice.get("/api/files").json["files"][0]
    assert f["name"] == "گزارش.pdf" and f["size"] == 8 and f["mime_type"] == "application/pdf"
    cd = alice.get(f["download_url"]).headers["Content-Disposition"]
    assert f"filename*=UTF-8''{quote('گزارش.pdf')}" in cd
    # stored on disk under a UUID in the user's directory, never the display name
    user_dir = os.path.join(app.config["UPLOAD_FOLDER"], "2")  # admin is id 1, alice id 2
    (stored,) = os.listdir(user_dir)
    assert len(stored) == 32 and stored.isalnum()


def test_duplicate_names_get_suffix_no_overwrite(two_users):
    alice, _ = two_users
    upload(alice, "a.txt", b"one")
    assert upload(alice, "a.txt", b"two").json["filename"] == "a (1).txt"
    files = {f["name"]: f for f in alice.get("/api/files").json["files"]}
    assert alice.get(files["a.txt"]["download_url"]).data == b"one"
    assert alice.get(files["a (1).txt"]["download_url"]).data == b"two"


def test_same_name_different_users_ok(two_users):
    alice, bob = two_users
    assert upload(alice, "a.txt").json["filename"] == "a.txt"
    assert upload(bob, "a.txt").json["filename"] == "a.txt"


def test_delete_removes_file_from_disk(two_users, app):
    alice, _ = two_users
    upload(alice, "a.txt")
    f = alice.get("/api/files").json["files"][0]
    assert alice.delete(f"/api/files/{f['id']}").json["success"]  # to the trash: still on disk
    assert len(os.listdir(os.path.join(app.config["UPLOAD_FOLDER"], "2"))) == 1
    assert alice.delete(f"/api/trash/{f['id']}").json["success"]
    assert os.listdir(os.path.join(app.config["UPLOAD_FOLDER"], "2")) == []


def test_foreign_keys_and_wal(app):
    from simplecloud.db import connect
    conn = connect(app.config["DATABASE"])
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO files (user_id, name, stored_name, size, mime_type) VALUES (999, 'x', 'y', 0, 'z')")
