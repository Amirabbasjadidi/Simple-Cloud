import io

from conftest import upload

JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 50
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 20


def id3(frame_body, version=3):
    size = len(frame_body)
    fsize = size.to_bytes(4, "big") if version == 3 else bytes(((size >> s) & 0x7F) for s in (21, 14, 7, 0))
    frame = b"APIC" + fsize + b"\0\0" + frame_body
    tsize = len(frame)
    return b"ID3" + bytes([version, 0, 0]) + bytes(((tsize >> s) & 0x7F) for s in (21, 14, 7, 0)) + frame + b"audio"


def file_entry(client, name):
    return [f for f in client.get("/api/files").json["files"] if f["name"] == name][0]


def test_pdf_thumb_upload_by_owner_only(two_users):
    alice, bob = two_users
    fid = upload(alice, "a.pdf", b"%PDF-1.4").json["id"]
    assert file_entry(alice, "a.pdf")["thumb"] == "missing"
    assert alice.get(f"/files/{fid}/thumb").status_code == 404
    assert bob.put(f"/files/{fid}/thumb", data=JPEG).status_code == 404
    assert alice.put(f"/files/{fid}/thumb", data=b"<svg>").status_code == 400  # JPEG only
    assert alice.put(f"/files/{fid}/thumb", data=JPEG).json["thumb"] == "ready"
    r = alice.get(f"/files/{fid}/thumb")
    assert r.data == JPEG and r.mimetype == "image/jpeg" and "sandbox" in r.headers["Content-Security-Policy"]
    assert file_entry(alice, "a.pdf")["thumb"] == "ready"


def test_failed_generation_is_remembered(two_users):
    alice, _ = two_users
    fid = upload(alice, "v.mp4", b"\0").json["id"]
    assert alice.put(f"/files/{fid}/thumb", data=b"").json["thumb"] == "none"
    assert file_entry(alice, "v.mp4")["thumb"] == "none"
    txt = upload(alice, "t.txt", b"x").json["id"]
    assert alice.put(f"/files/{txt}/thumb", data=JPEG).status_code == 400  # no thumbnails for text
    assert file_entry(alice, "t.txt")["thumb"] is None


def test_mp3_cover_v23_utf16_description_and_v24(two_users):
    alice, _ = two_users
    body = b"\x01" + b"image/png\0" + b"\x03" + "Cover".encode("utf-16") + b"\0\0" + PNG
    fid = upload(alice, "song.mp3", id3(body)).json["id"]
    r = alice.get(f"/files/{fid}/thumb")
    assert r.status_code == 200 and r.data == PNG and r.mimetype == "image/png"
    fid = upload(alice, "song4.mp3", id3(b"\0image/jpeg\0\x03desc\0" + JPEG, version=4)).json["id"]
    assert alice.get(f"/files/{fid}/thumb").data == JPEG
    fid = upload(alice, "plain.mp3", b"\xff\xfbnot a tag").json["id"]
    assert alice.get(f"/files/{fid}/thumb").status_code == 404
    assert file_entry(alice, "plain.mp3")["thumb"] == "none"


def test_thumb_removed_with_file(two_users, app):
    import os
    alice, _ = two_users
    fid = upload(alice, "a.pdf", b"%PDF").json["id"]
    alice.put(f"/files/{fid}/thumb", data=JPEG)
    alice.delete(f"/api/files/{fid}")
    alice.delete(f"/api/trash/{fid}")
    assert os.listdir(os.path.join(app.config["UPLOAD_FOLDER"], "2")) == []
