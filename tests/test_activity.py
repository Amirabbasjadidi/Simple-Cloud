import time

from conftest import upload
from test_admin import setup


def test_activity_log_records_and_filters(app):
    boss, alice, users = setup(app)
    upload(alice, "report.txt")
    fid = alice.get("/api/files").json["files"][0]["id"]
    alice.delete(f"/api/files/{fid}")
    boss.patch(f"/api/admin/users/{users['alice']['id']}", json={"quota_mb": 5})
    r = boss.get("/api/admin/events").json
    events = [(e["event"], e["user"]) for e in r["events"]]
    assert ("upload", "alice") in events and ("delete", "alice") in events
    assert ("admin_update_user", "boss") in events  # the actor, not the target
    assert {"upload", "delete", "login_ok"} <= set(r["event_types"]) and "alice" in r["users"]
    assert {e["event"] for e in boss.get("/api/admin/events?user=alice").json["events"]} >= {"upload", "delete"}
    assert all(e["event"] == "upload" for e in boss.get("/api/admin/events?event=upload").json["events"])
    hit = boss.get("/api/admin/events?q=report").json["events"]
    assert hit and all("report" in str(e["details"]) for e in hit)
    assert boss.get("/api/admin/events?q=100%25_").json["events"] == []  # LIKE wildcards are literal
    assert boss.get(f"/api/admin/events?from={time.time() + 60}").json["events"] == []
    assert boss.get("/api/admin/events?from=x").status_code == 400
    assert alice.get("/api/admin/events").status_code == 403


def test_activity_paging(app):
    boss, _, _ = setup(app)
    from simplecloud import admin
    with app.app_context():
        from simplecloud.db import get_db
        db = get_db()
        db.executemany("INSERT INTO events (ts, event, username) VALUES (?, 'x', 'u')",
                       [(time.time(),)] * (admin.EVENTS_PAGE + 5))
        db.commit()
    first = boss.get("/api/admin/events?event=x").json
    assert len(first["events"]) == admin.EVENTS_PAGE and first["next"]
    rest = boss.get(f"/api/admin/events?event=x&before={first['next']}").json
    assert len(rest["events"]) == 5 and rest["next"] is None
