import logging

from conftest import add_user, login, upload


def test_audit_events(app, caplog):
    caplog.set_level(logging.INFO, logger=app.logger.name)
    add_user(app, "alice")
    c = app.test_client()
    login(app, c, "alice", "wrong")
    login(app, c, "alice")
    upload(c, "گزارش 1.txt")
    fid = c.get("/api/files").json["files"][0]["id"]
    c.delete(f"/api/files/{fid}")
    c.post("/logout")
    lines = [r.getMessage() for r in caplog.records]
    assert any(l.startswith("event=login_failed") and "reason=credentials" in l and "user=alice" in l for l in lines)
    assert any(l.startswith("event=login_ok user=alice") for l in lines)
    assert any(l.startswith("event=upload") and 'name="گزارش 1.txt"' in l for l in lines)
    assert any(l.startswith("event=delete") for l in lines)
    assert any(l.startswith("event=logout") for l in lines)
    assert all("ip=" in l for l in lines if l.startswith("event="))


def test_log_values_cannot_inject_lines(app, caplog):
    caplog.set_level(logging.INFO, logger=app.logger.name)
    c = app.test_client()
    login(app, c, "x\nevent=login_ok user=admin", "wrong")
    msg = next(r.getMessage() for r in caplog.records if "login_failed" in r.getMessage())
    assert "\n" not in msg
