"""Tiny i18n: English source strings, one JSON dictionary per extra language.

The language comes from the `lang` cookie, else DEFAULT_LANG (Persian by default). Templates call
`_("English text", name=value)`; page scripts get the same dictionary as JSON and call `SC.t`.
"""
import json
import os
from urllib.parse import urlsplit

from flask import Blueprint, current_app, g, has_request_context, redirect, request

LANGUAGES = {
    "fa": {"name": "فارسی", "dir": "rtl"},
    "en": {"name": "English", "dir": "ltr"},
}
COOKIE = "lang"
_HERE = os.path.dirname(__file__)
_catalogs = {}

bp = Blueprint("i18n", __name__)


def catalog(lang):
    if lang == "en":
        return {}
    if lang not in _catalogs:
        with open(os.path.join(_HERE, "translations", f"{lang}.json"), encoding="utf-8") as f:
            _catalogs[lang] = json.load(f)
    return _catalogs[lang]


def current_lang():
    if not has_request_context():
        return "en"  # CLI and background jobs
    if "lang" not in g:
        lang = request.cookies.get(COOKIE)
        g.lang = lang if lang in LANGUAGES else current_app.config["DEFAULT_LANG"]
    return g.lang


_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def gettext(text, **kwargs):
    lang = current_lang()
    s = catalog(lang).get(text) or text
    if lang == "fa":  # numbers in Persian sentences use Persian digits
        kwargs = {k: str(v).translate(_FA_DIGITS) if isinstance(v, int) else v for k, v in kwargs.items()}
    return s % kwargs if kwargs else s


def _safe_next(target):
    """Only same-site relative paths, so the switcher cannot be used as an open redirect."""
    target = target or ""
    parts = urlsplit(target)
    # Browsers read "/\host" like "//host", so backslashes are refused too.
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


@bp.get("/lang/<code>")
def set_language(code):
    resp = redirect(_safe_next(request.args.get("next")))
    if code in LANGUAGES:
        resp.set_cookie(COOKIE, code, max_age=365 * 86400, samesite="Lax", httponly=True,
                        secure=current_app.config["SESSION_COOKIE_SECURE"])
    return resp


def init_app(app):
    if app.config["DEFAULT_LANG"] not in LANGUAGES:
        raise RuntimeError(f"DEFAULT_LANG must be one of {', '.join(LANGUAGES)}")
    app.register_blueprint(bp)
    app.jinja_env.globals["_"] = gettext

    @app.context_processor
    def i18n_context():
        lang = current_lang()
        return {"lang": lang, "lang_dir": LANGUAGES[lang]["dir"], "languages": LANGUAGES,
                "i18n_json": json.dumps(catalog(lang), ensure_ascii=False).replace("</", "<\\/")}
