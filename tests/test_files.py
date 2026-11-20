from conftest import upload


def _ids(client):
    return [f["id"] for f in client.get("/api/files").json["files"]]


def test_isolation_between_users(two_users):
    alice, bob = two_users
    assert upload(alice, "secret.txt", b"alice-data").json["success"]
    (fid,) = _ids(alice)
    assert _ids(bob) == []
    assert bob.get(f"/files/{fid}/download").status_code == 404
    assert bob.get(f"/files/{fid}/preview").status_code == 404
    assert bob.delete(f"/api/files/{fid}").status_code == 404
    # still there for alice
    assert alice.get(f"/files/{fid}/download").data == b"alice-data"


def test_old_routes_removed(two_users):
    alice, _ = two_users
    upload(alice, "a.txt")
    assert alice.get("/uploads/a.txt").status_code == 404
    assert alice.get("/media/a.txt").status_code == 404


def test_requires_login(app):
    c = app.test_client()
    assert c.get("/files/1/download").status_code == 302
    assert c.get("/api/files").status_code == 401  # JSON APIs answer 401, pages redirect


def test_download_is_attachment(two_users):
    alice, _ = two_users
    upload(alice, "pic.png", b"\x89PNG")
    (fid,) = _ids(alice)
    r = alice.get(f"/files/{fid}/download")
    assert r.headers["Content-Disposition"].startswith("attachment")


def test_preview_safe_type_inline(two_users):
    alice, _ = two_users
    upload(alice, "pic.png", b"\x89PNG")
    (fid,) = _ids(alice)
    r = alice.get(f"/files/{fid}/preview")
    assert r.headers["Content-Disposition"].startswith("inline") and r.mimetype == "image/png"
    assert "sandbox" in r.headers["Content-Security-Policy"]


def test_preview_html_svg_forced_download(two_users):
    alice, _ = two_users
    upload(alice, "evil.html", b"<script>alert(1)</script>")
    upload(alice, "evil.svg", b"<svg onload=alert(1)>")
    for fid in _ids(alice):
        r = alice.get(f"/files/{fid}/preview")
        assert r.headers["Content-Disposition"].startswith("attachment")
        assert r.mimetype == "application/octet-stream"


def test_upload_size_limit(make_app):
    from conftest import add_user, login
    app = make_app(MAX_CONTENT_LENGTH=1024)
    add_user(app, "alice")
    c = app.test_client()
    login(app, c, "alice")
    r = upload(c, "big.bin", b"x" * 4096)
    assert r.status_code == 413 and r.is_json and not r.json["success"]
    assert upload(c, "small.bin", b"x" * 100).json["success"]
