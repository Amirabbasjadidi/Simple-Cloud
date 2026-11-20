import io
import os

from conftest import upload


def mkdir(client, name, parent=None):
    r = client.post("/api/folders", json={"name": name, "parent_id": parent})
    assert r.json["success"], r.json
    return r.json["folder"]["id"]


def up(client, name, folder=None, data=b"x"):
    form = {"file": (io.BytesIO(data), name)}
    if folder:
        form["folder_id"] = str(folder)
    return client.post("/upload", data=form, content_type="multipart/form-data").json


def listing(client, **params):
    return client.get("/api/files", query_string=params).json


def test_create_list_and_breadcrumbs(two_users):
    alice, _ = two_users
    docs = mkdir(alice, "Docs")
    sub = mkdir(alice, "سال ۱۴۰۳", docs)
    up(alice, "a.txt", sub)
    up(alice, "root.txt")
    root = listing(alice)
    assert [f["name"] for f in root["folders"]] == ["Docs"] and [f["name"] for f in root["files"]] == ["root.txt"]
    inner = listing(alice, folder=sub)
    assert [c["name"] for c in inner["breadcrumbs"]] == ["Docs", "سال ۱۴۰۳"]
    assert [f["name"] for f in inner["files"]] == ["a.txt"]


def test_duplicate_folder_name_rejected(two_users):
    alice, _ = two_users
    mkdir(alice, "Docs")
    assert alice.post("/api/folders", json={"name": "Docs"}).status_code == 409
    assert alice.post("/api/folders", json={"name": "  "}).status_code == 400


def test_same_file_name_in_different_folders(two_users):
    alice, _ = two_users
    a = mkdir(alice, "A")
    assert up(alice, "x.txt")["filename"] == "x.txt"
    assert up(alice, "x.txt", a)["filename"] == "x.txt"
    assert up(alice, "x.txt", a)["filename"] == "x (1).txt"


def test_folder_isolation(two_users):
    alice, bob = two_users
    a = mkdir(alice, "Private")
    up(alice, "s.txt", a)
    assert bob.get("/api/files", query_string={"folder": a}).status_code == 404
    assert bob.post("/api/folders", json={"name": "x", "parent_id": a}).status_code == 404
    assert bob.patch(f"/api/folders/{a}", json={"name": "pwned"}).status_code == 404
    assert bob.delete(f"/api/folders/{a}").status_code == 404
    r = bob.post("/upload", data={"file": (io.BytesIO(b"x"), "y.txt"), "folder_id": str(a)},
                 content_type="multipart/form-data")
    assert r.status_code == 404
    bf = up(bob, "b.txt")["id"]
    assert bob.patch(f"/api/files/{bf}", json={"folder_id": a}).status_code == 404
    assert listing(alice, folder=a)["files"][0]["name"] == "s.txt"


def test_rename_and_move_file(two_users):
    alice, _ = two_users
    a = mkdir(alice, "A")
    fid = up(alice, "x.txt")["id"]
    up(alice, "y.txt", a)
    r = alice.patch(f"/api/files/{fid}", json={"name": "گزارش.pdf"})
    assert r.json["file"]["mime_type"] == "application/pdf"
    assert alice.patch(f"/api/files/{fid}", json={"name": "y.txt", "folder_id": a}).status_code == 409
    assert alice.patch(f"/api/files/{fid}", json={"folder_id": a}).json["success"]
    assert {f["name"] for f in listing(alice, folder=a)["files"]} == {"y.txt", "گزارش.pdf"}
    assert alice.patch(f"/api/files/{fid}", json={"folder_id": None}).json["success"]
    assert [f["name"] for f in listing(alice)["files"]] == ["گزارش.pdf"]


def test_move_folder_no_cycles(two_users):
    alice, _ = two_users
    a = mkdir(alice, "A")
    b = mkdir(alice, "B", a)
    c = mkdir(alice, "C", b)
    assert alice.patch(f"/api/folders/{a}", json={"parent_id": c}).status_code == 400
    assert alice.patch(f"/api/folders/{a}", json={"parent_id": a}).status_code == 400
    assert alice.patch(f"/api/folders/{c}", json={"parent_id": None}).json["success"]
    assert {f["name"] for f in listing(alice)["folders"]} == {"A", "C"}


def test_delete_folder_recursive(two_users, app):
    alice, _ = two_users
    a = mkdir(alice, "A")
    b = mkdir(alice, "B", a)
    up(alice, "1.txt", a)
    up(alice, "2.txt", b)
    keep = up(alice, "keep.txt")["id"]
    r = alice.delete(f"/api/folders/{a}").json
    assert r["deleted_files"] == 2 and r["deleted_folders"] == 2
    assert [f["id"] for f in listing(alice)["files"]] == [keep]
    assert len(os.listdir(os.path.join(app.config["UPLOAD_FOLDER"], "2"))) == 1


def test_search_and_sort(two_users):
    alice, bob = two_users
    a = mkdir(alice, "Reports")
    up(alice, "report-big.txt", a, b"x" * 300)
    up(alice, "Report_small.txt", None, b"x" * 10)
    up(alice, "other.txt", None, b"x" * 100)
    up(bob, "report-bob.txt")
    r = listing(alice, q="report")
    assert r["search"] and {f["name"] for f in r["files"]} == {"report-big.txt", "Report_small.txt"}
    assert [f["name"] for f in r["folders"]] == ["Reports"]
    assert listing(alice, q="%")["files"] == []  # LIKE wildcards are escaped
    assert [f["name"] for f in listing(alice, q="t_s")["files"]] == ["Report_small.txt"]
    names = lambda **p: [f["name"] for f in listing(alice, **p)["files"]]
    assert names(sort="size", order="desc") == ["other.txt", "Report_small.txt"]
    assert names(sort="name", order="asc") == ["other.txt", "Report_small.txt"]
    assert names(sort="date", order="desc") == ["other.txt", "Report_small.txt"]
    assert names(sort="bogus") == ["other.txt", "Report_small.txt"]  # falls back to date


def test_files_page_renders(two_users):
    alice, _ = two_users
    assert b'id="listing"' in alice.get("/files").data
