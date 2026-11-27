import io
import zipfile

from conftest import upload


def up(client, name, data, folder=None):
    return client.post("/upload", data={"file": (io.BytesIO(data), name), "folder_id": folder or ""},
                       content_type="multipart/form-data").json["id"]


def test_zip_files_and_folder_tree(two_users):
    alice, bob = two_users
    top = alice.post("/api/folders", json={"name": "پروژه"}).json["folder"]["id"]
    sub = alice.post("/api/folders", json={"name": "sub", "parent_id": top}).json["folder"]["id"]
    up(alice, "a.txt", b"A", top)
    up(alice, "b.txt", b"B", sub)
    trashed = up(alice, "gone.txt", b"G", top)
    alice.delete(f"/api/files/{trashed}")
    loose = up(alice, "c.txt", b"C")
    r = alice.get(f"/files/zip?folders={top}&files={loose}")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.data))
    assert {i.filename: z.read(i) for i in z.infolist()} == {"پروژه/a.txt": b"A", "پروژه/sub/b.txt": b"B", "c.txt": b"C"}
    assert z.testzip() is None
    r = alice.get(f"/files/zip?folders={top}")
    assert "filename*=UTF-8''%D9%BE" in r.headers["Content-Disposition"]
    assert bob.get(f"/files/zip?files={loose}").status_code == 404
    assert bob.get(f"/files/zip?folders={top}").status_code == 404
    assert alice.get("/files/zip").status_code == 400


def test_zip_same_name_kept_twice(two_users):
    alice, _ = two_users
    f1 = alice.post("/api/folders", json={"name": "x"}).json["folder"]["id"]
    a = up(alice, "same.txt", b"1")
    b = up(alice, "same.txt", b"2", f1)
    z = zipfile.ZipFile(io.BytesIO(alice.get(f"/files/zip?files={a},{b}").data))
    assert sorted(z.namelist()) == ["same (1).txt", "same.txt"]
