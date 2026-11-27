"""Audit log: one `event=<name> key=value ...` line per event in the service log (easy to grep in
journalctl), and the same event in the `events` table for the admin dashboard's activity log."""
import json
import logging
import time

from flask import current_app, has_app_context, has_request_context, request

from .db import get_db


def _fmt(value):
    s = str(value)
    if not s or not s.isprintable() or any(c in s for c in ' "=\\'):
        return json.dumps(s, ensure_ascii=False)
    return s


def log_event(event, level=logging.INFO, **fields):
    if has_request_context():
        fields.setdefault("ip", request.remote_addr)
    current_app.logger.log(level, " ".join([f"event={event}"] + [f"{k}={_fmt(v)}" for k, v in fields.items()]))
    if has_app_context():
        _store(event, dict(fields))


def _store(event, fields):
    ip = fields.pop("ip", None)
    # Admin actions name the actor `admin=` and the target `user=`: the actor is who did it.
    user = fields.pop("admin") if "admin" in fields else fields.pop("user", None)
    try:
        db = get_db()
        in_tx = db.in_transaction  # inside the caller's transaction: it commits (or rolls back) with it
        db.execute("INSERT INTO events (ts, event, username, ip, details) VALUES (?, ?, ?, ?, ?)",
                   (time.time(), event, None if user is None else str(user), ip,
                    json.dumps(fields, ensure_ascii=False, default=str) if fields else None))
        if not in_tx:
            db.commit()
    except Exception:  # the activity log must never break the action it records
        current_app.logger.warning("could not store event %s in the activity log", event, exc_info=True)
