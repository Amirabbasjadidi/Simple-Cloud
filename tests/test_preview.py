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
    assert ("default-src" in r.headers["Content-Security-Policy"]) == (kind != "pdf")


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


def test_pdf_raw_bytes_for_viewer(two_users):
    """?raw=1 (pdf.js viewer): same bytes as text/plain with no filename, so download managers
    such as IDM don't take the request over. Ignored for other kinds."""
    alice, _ = two_users
    upload(alice, "a.pdf", b"%PDF-1.4 data")
    f = alice.get("/api/files").json["files"][0]
    r = alice.get(f["preview_url"] + "?raw=1")
    assert r.data == b"%PDF-1.4 data" and r.headers["Content-Type"].startswith("text/plain")
    assert "Content-Disposition" not in r.headers
    assert alice.get(f["preview_url"] + "?raw=1", headers={"Range": "bytes=1-3"}).data == b"PDF"
    upload(alice, "b.png", b"\x89PNG")
    png = [x for x in alice.get("/api/files").json["files"] if x["name"] == "b.png"][0]
    assert alice.get(png["preview_url"] + "?raw=1").headers["Content-Type"] == "image/png"
