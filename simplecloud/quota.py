"""Per-user storage quota. quota_bytes NULL = unlimited.

Usage counts stored files plus the declared size of unfinished chunked uploads (reserved),
so parallel uploads can't overshoot the quota.
"""


class QuotaError(Exception):
    pass


def usage(db, user_id, exclude_upload=None):
    used = db.execute("SELECT COALESCE(SUM(size), 0) FROM files WHERE user_id = ?", (user_id,)).fetchone()[0]
    reserved = db.execute("SELECT COALESCE(SUM(total_size), 0) FROM uploads WHERE user_id = ? AND id IS NOT ?",
                          (user_id, exclude_upload)).fetchone()[0]
    quota = db.execute("SELECT quota_bytes FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    return {"used": used, "reserved": reserved, "quota": quota}


def remaining(db, user_id, exclude_upload=None):
    u = usage(db, user_id, exclude_upload)
    return None if u["quota"] is None else max(0, u["quota"] - u["used"] - u["reserved"])


def check(db, user_id, extra, exclude_upload=None):
    left = remaining(db, user_id, exclude_upload)
    if left is not None and extra > left:
        raise QuotaError("Not enough storage space: this would exceed your quota.")
