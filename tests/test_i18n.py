import glob
import json
import os
import re

from conftest import add_user, login

ROOT = os.path.dirname(os.path.dirname(__file__))
FA = json.load(open(os.path.join(ROOT, "simplecloud", "translations", "fa.json"), encoding="utf-8"))


def html_tag(page):
    return re.search(r"<html[^>]*>", page).group(0)


def test_persian_is_the_default(app):
    page = app.test_client().get("/login").get_data(as_text=True)
    assert 'lang="fa"' in html_tag(page) and 'dir="rtl"' in html_tag(page)
    assert FA["Sign in"] in page and FA["Username"] in page
    # the page scripts get the same dictionary
    data = re.search(r'<script type="application/json" id="i18n-data">(.*?)</script>', page, re.S).group(1)
    assert json.loads(data)["Upload"] == FA["Upload"]


def test_switch_to_english(app):
    c = app.test_client()
    r = c.get("/lang/en?next=/login")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")
    cookie = r.headers["Set-Cookie"]
    assert cookie.startswith("lang=en") and "HttpOnly" in cookie and "Secure" not in cookie
    page = c.get("/login").get_data(as_text=True)
    assert 'lang="en"' in html_tag(page) and 'dir="ltr"' in html_tag(page)
    assert ">Sign in<" in page and json.loads(re.search(r'id="i18n-data">(.*?)</script>', page).group(1)) == {}
    # the switcher offers the other language
    assert 'href="/lang/fa?next=/login"' in page


def test_default_language_setting(make_app):
    page = make_app(DEFAULT_LANG="en").test_client().get("/login").get_data(as_text=True)
    assert 'dir="ltr"' in html_tag(page)


def test_language_switch_is_not_an_open_redirect(app):
    c = app.test_client()
    for bad in ("//evil.example", "https://evil.example/x", "evil", "/\\evil.example"):
        assert c.get("/lang/en", query_string={"next": bad}).headers["Location"] == "/", bad
    assert "Set-Cookie" not in c.get("/lang/xx?next=/").headers


def test_rtl_pages_render_for_logged_in_user(app):
    add_user(app, "alice")
    c = app.test_client()
    assert login(app, c, "alice").json["success"]
    for path in ("/files", "/upload", "/download", "/shares", "/account"):
        page = c.get(path).get_data(as_text=True)
        assert 'dir="rtl"' in html_tag(page), path
        assert FA["Log out"] in page and FA["Files"] in page, path


def test_server_messages_are_translated(make_app):
    app = make_app(LOGIN_MAX_FAILURES_USER=1, LOGIN_MAX_FAILURES_IP=100)
    add_user(app, "alice")
    c = app.test_client()
    login(app, c, "alice", password="wrong")
    msg = login(app, c, "alice").json["error"]
    assert msg.startswith(FA["Too many failed attempts. Try again in %(n)s seconds."].split("%")[0])
    assert re.search("[۰-۹]", msg)  # Persian digits
    c.set_cookie("lang", "en")
    assert login(app, c, "alice").json["error"].startswith("Too many failed attempts. Try again in ")


def test_cli_messages_stay_english(app):
    add_user(app, "alice")
    r = app.test_cli_runner().invoke(args=["adduser", "--username", "alice"], env={"SIMPLECLOUD_PASSWORD": "pw-xxxxxxxx"})
    assert "already exists" in r.output


def _ui_strings():
    keys = set()
    for p in glob.glob(os.path.join(ROOT, "templates", "*.html")):
        s = open(p, encoding="utf-8").read()
        keys |= set(re.findall(r'_\("((?:[^"\\]|\\.)*)"', s)) | set(re.findall(r"_\('((?:[^'\\]|\\.)*)'", s))
        keys |= set(re.findall(r"\('[\w.]+', '([^']+)', '[\w-]+'\)", s))  # navigation labels
    for p in glob.glob(os.path.join(ROOT, "static", "*.js")):
        s = open(p, encoding="utf-8").read()
        keys |= set(re.findall(r'\b[Tt]\("((?:[^"\\]|\\.)*)"', s))
        keys |= set(re.findall(r'\[\s*"([A-Z][^"]+)",\s*"[a-z-]+",', s))  # menu entries
    for p in glob.glob(os.path.join(ROOT, "simplecloud", "*.py")):
        if p.endswith("i18n.py"):
            continue  # its docstring shows an example call
        keys |= set(re.findall(r'\b_\("((?:[^"\\]|\\.)*)"', open(p, encoding="utf-8").read()))
    return keys


def test_every_ui_string_has_a_persian_translation():
    keys = _ui_strings()
    assert len(keys) > 150
    missing = sorted(k for k in keys if k not in FA)
    assert not missing, missing
    # placeholders survive translation
    for k, v in FA.items():
        assert re.findall(r"%\(\w+\)s", k) == re.findall(r"%\(\w+\)s", v), k


def test_no_external_assets(app):
    """Internal network: nothing may load from a CDN, and the CSP would block it anyway."""
    page = app.test_client().get("/login").get_data(as_text=True)
    css = open(os.path.join(ROOT, "static", "app.css"), encoding="utf-8").read()
    for text in (page, css):
        assert not re.search(r"""(src|href)=["']?https?://|url\(["']?https?://|@import""", text)
    for f in ("fonts/vazirmatn/Vazirmatn-wght.woff2", "fonts/vazirmatn/OFL.txt",
              "fonts/fira-sans/FiraSans-Regular.woff2", "fonts/fira-sans/OFL.txt"):
        assert app.test_client().get("/static/" + f).status_code == 200, f


def test_theme_toggle_names_the_action(app):
    page = app.test_client().get("/login").get_data(as_text=True)
    button = re.search(r'<button[^>]*data-action="theme".*?</button>', page, re.S).group(0)
    # both labels are rendered; CSS shows the one for the current theme (no flash before app.js)
    assert f'data-label-dark="{FA["Dark mode"]}"' in button and f'data-label-light="{FA["Light mode"]}"' in button
    assert '<span class="theme-dark">' in button and '<span class="theme-light">' in button
    assert FA["Dark mode"] == "حالت تاریک" and FA["Light mode"] == "حالت روشن"
