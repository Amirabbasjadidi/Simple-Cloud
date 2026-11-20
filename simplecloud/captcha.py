"""Math captcha. The answer is stored server-side in SQLite, keyed by a random token kept in the session."""
import base64
import os
import random
import secrets
import time
from io import BytesIO

from flask import current_app, session
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .db import get_db

rng = random.SystemRandom()


def _challenge():
    operator = rng.choice(["+", "-", "*"])
    if operator == "-":
        a = rng.randint(1, 20)
        b = rng.randint(1, a)
        return f"{a} - {b}", a - b
    if operator == "*":
        a, b = rng.randint(1, 5), rng.randint(1, 5)
        return f"{a} * {b}", a * b
    a, b = rng.randint(1, 10), rng.randint(1, 10)
    return f"{a} + {b}", a + b


def _render(text):
    font_path = os.path.join(current_app.root_path, "static", "Ubuntu.ttf")
    img = Image.new("RGB", (200, 100), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(font_path, 50)
    except OSError:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    x = (200 - (bbox[2] - bbox[0])) // 2
    y = (100 - (bbox[3] - bbox[1])) // 2
    text_img = Image.new("RGBA", (200, 100), (255, 255, 255, 0))
    ImageDraw.Draw(text_img).text(
        (x, y), text, font=font, fill=(rng.randint(0, 100), rng.randint(0, 100), rng.randint(0, 100))
    )
    text_img = text_img.rotate(rng.randint(-25, 25), resample=Image.BICUBIC, center=(100, 50))
    img.paste(text_img, (0, 0), text_img)

    for _ in range(10):
        draw.line([rng.randint(0, 200) for _ in range(4)],
                  fill=(rng.randint(50, 150), rng.randint(50, 150), rng.randint(50, 150)), width=3)
    for _ in range(300):
        draw.point((rng.randint(0, 200), rng.randint(0, 100)),
                   fill=(rng.randint(150, 255), rng.randint(150, 255), rng.randint(150, 255)))
    img = img.filter(ImageFilter.GaussianBlur(1))

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def new_captcha():
    """Create a captcha, replace any previous one for this session, return base64 PNG."""
    text, answer = _challenge()
    token = secrets.token_urlsafe(24)
    now = time.time()
    db = get_db()
    old = session.get("captcha_token")
    db.execute("DELETE FROM captchas WHERE expires_at < ? OR token = ?", (now, old))
    db.execute("INSERT INTO captchas (token, answer, expires_at) VALUES (?, ?, ?)",
               (token, answer, now + current_app.config["CAPTCHA_TTL_SECONDS"]))
    db.commit()
    session["captcha_token"] = token
    return _render(text)


def consume_captcha(raw_input):
    """Check the answer and always invalidate the captcha (single use, success or failure)."""
    token = session.pop("captcha_token", None)
    if not token:
        return False
    db = get_db()
    row = db.execute("SELECT answer, expires_at FROM captchas WHERE token = ?", (token,)).fetchone()
    deleted = db.execute("DELETE FROM captchas WHERE token = ?", (token,)).rowcount
    db.commit()
    if row is None or deleted != 1 or row["expires_at"] < time.time():
        return False
    try:
        return int(str(raw_input).strip()) == row["answer"]
    except (TypeError, ValueError):
        return False
