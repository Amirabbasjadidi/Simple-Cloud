"""Structured audit log: one `event=<name> key=value ...` line per event (easy to grep in journalctl)."""
import json
import logging

from flask import current_app, has_request_context, request


def _fmt(value):
    s = str(value)
    if not s or not s.isprintable() or any(c in s for c in ' "=\\'):
        return json.dumps(s, ensure_ascii=False)
    return s


def log_event(event, level=logging.INFO, **fields):
    if has_request_context():
        fields.setdefault("ip", request.remote_addr)
    current_app.logger.log(level, " ".join([f"event={event}"] + [f"{k}={_fmt(v)}" for k, v in fields.items()]))
