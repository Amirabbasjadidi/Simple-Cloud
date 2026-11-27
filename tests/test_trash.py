import os
import time

from conftest import upload


def ids(client, path="/api/files"):
    return [f["id"] for f in client.get(path).json["files"]]


def test_trash_keeps_quota_until_deleted_for_good(two_users, app):
    alice, bob = two_users
    fid = upload(alice, "a.txt", b"x" * 100).json["id"]
    assert alice.delete(f"/api/files/{fid}").json["success"]
    assert ids(alice) == [] and ids(alice, "/api/trash") == [fid]
    assert alice.get("/api/usage").json["used"] == 100  # still counts toward the quota
    assert alice.get(f"/files/{fid}/download").status_code == 404  # not usable while in the trash
    assert bob.delete(f"/api/trash/{fid}").status_code == 404  # only the owner
    assert alice.delete(f"/api/trash/{fid}").json["success"]
    assert alice.get("/api/usage").json["used"] == 0


def test_restore_to_folder_or_root_and_rename_on_clash(two_users):
    alice, _ = two_users
    folder = alice.post("/api/folders", json={"name": "Docs"}).json["folder"]["id"]
    up = lambda name: alice.post("/upload", data={"file": (__import__("io").BytesIO(b"1"), name), "folder_id": folder},
                                 content_type="multipart/form-data").json["id"]
    a = up("a.txt")
    alice.delete(f"/api/files/{a}")
    assert alice.get("/api/trash").json["files"][0]["folder"] == "Docs"
    b = up("a.txt")  # same name allowed while the first is in the trash
    r = alice.post(f"/api/trash/{a}/restore").json
    assert r["folder_id"] == folder and r["name"] == "a (1).txt"
    alice.delete(f"/api/files/{b}")
    alice.delete(f"/api/folders/{folder}")  # folder gone: restore goes to the root
    r = alice.post(f"/api/trash/{b}/restore").json
    assert r["folder_id"] is None and ids(alice) == [b]


def test_auto_purge_after_trash_days(two_users, app, monkeypatch):
    alice, _ = two_users
    fid = upload(alice, "a.txt").json["id"]
    alice.delete(f"/api/files/{fid}")
    from simplecloud import trash
    with app.app_context():
        from simplecloud.db import get_db
        assert trash.purge_trash(get_db(), app.config) == 0
        later = time.time() + app.config["TRASH_DAYS"] * 86400 + 1
        assert trash.purge_trash(get_db(), app.config, now=later) == 1
    assert ids(alice, "/api/trash") == []
    assert os.listdir(os.path.join(app.config["UPLOAD_FOLDER"], "2")) == []


def test_bulk_delete_and_empty(two_users):
    alice, bob = two_users
    a = upload(alice, "a.txt").json["id"]
    b = upload(alice, "b.txt").json["id"]
    folder = alice.post("/api/folders", json={"name": "F"}).json["folder"]["id"]
    other = upload(bob, "x.txt").json["id"]
    assert alice.post("/api/items/delete", json={"files": [other]}).status_code == 404
    r = alice.post("/api/items/delete", json={"files": [a, b], "folders": [folder]}).json
    assert r["deleted_files"] == 2 and ids(alice) == []
    assert alice.delete("/api/trash").json["deleted"] == 2
    assert ids(bob) == [other]
