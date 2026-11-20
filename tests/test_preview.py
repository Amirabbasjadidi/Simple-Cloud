import pytest

from conftest import upload


@pytest.mark.parametrize("name,data,kind,ctype", [
    ("a.png", b"\x89PNG", "image", "image/png"),
    ("a.mp4", b"\x00", "video", "video/mp4"),
    ("a.mp3", b"\x00", "audio", "audio/mpeg"),
    ("a.pdf", b"%PDF", "pdf", "application/pdf"),
    ("a.txt", "سلام".encode(), "text", "text/plain; charset=utf-8"),
    ("a.json", b"{}", "text", "text/plain; charset=utf-8"),
    ("a.html", b"<script>alert(1)</script>", "text", "text/plain; charset=utf-8"),
    ("a.svg", b"<svg onload=alert(1)>", "text", "text/plain; charset=utf-8"),
])
def test_preview_kinds(two_users, name, data, kind, ctype):
    alice, _ = two_users
    upload(alice, name, data)
    f = alice.get("/api/files").json["files"][0]
    assert f["preview_kind"] == kind
    r = alice.get(f["preview_url"])
    assert r.headers["Content-Type"] == ctype and r.data == data
    assert r.headers["Content-Disposition"].startswith("inline")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert ("sandbox" in r.headers["Content-Security-Policy"]) == (kind != "pdf")


def test_no_preview_for_binary(two_users):
    alice, _ = two_users
    upload(alice, "a.zip", b"PK")
    f = alice.get("/api/files").json["files"][0]
    assert f["preview_kind"] is None
    r = alice.get(f["preview_url"])
    assert r.headers["Content-Disposition"].startswith("attachment")
    assert r.headers["Content-Type"] == "application/octet-stream"


def test_range_requests_for_media_and_text(two_users):
    alice, _ = two_users
    upload(alice, "big.txt", b"0123456789" * 100)
    f = alice.get("/api/files").json["files"][0]
    r = alice.get(f["preview_url"], headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.data == b"0123456789"
    assert r.headers["Content-Range"] == "bytes 0-9/1000"
