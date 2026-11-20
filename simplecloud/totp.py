"""RFC 6238 TOTP (SHA-1, 6 digits, 30 s) with the standard library only."""
import base64
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30
DIGITS = 6


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def code_at(secret, step, digits=DIGITS):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), "sha1").digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** digits).zfill(digits)


def verify(secret, code, last_step=0, window=1, now=None):
    """Return the matched time step, or None. Steps <= last_step are rejected (no replay)."""
    code = "".join(str(code or "").split())
    if not (code.isdigit() and len(code) == DIGITS):
        return None
    current = int((time.time() if now is None else now) // STEP)
    for step in range(current - window, current + window + 1):
        if step > last_step and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def provisioning_uri(secret, username, issuer="Simple Cloud"):
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"
