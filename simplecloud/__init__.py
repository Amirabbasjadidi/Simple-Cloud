import logging
import os

from flask import Flask, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import ROOT, load_config

csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address)

CSP = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'")


def create_app(overrides=None):
    app = Flask(__name__, root_path=ROOT)
    app.config.update(load_config())
    if overrides:
        app.config.update(overrides)
    from flask.logging import default_handler
    default_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    app.logger.setLevel(app.config["LOG_LEVEL"])
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError(
            "SECRET_KEY is not set. Put SECRET_KEY=... in /etc/simplecloud.env "
            "(or SIMPLECLOUD_ENV_FILE) or the SECRET_KEY environment variable.")
    if app.config["PROXY_COUNT"]:
        n = app.config["PROXY_COUNT"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    from . import admin, auth, cli, db, files
    schema_ok = {"value": db.init_db(app)}
    if not schema_ok["value"]:
        app.logger.error("Database schema is out of date. Run `flask migrate --dry-run`, then `flask migrate`.")

    @app.before_request
    def require_current_schema():
        if not schema_ok["value"]:
            conn = db.connect(app.config["DATABASE"])
            schema_ok["value"] = db.schema_version(conn) == db.LATEST_VERSION
            conn.close()
            if not schema_ok["value"]:
                return "Database upgrade required: run `flask migrate` on the server.", 503

    app.teardown_appcontext(db.close_db)
    csrf.init_app(app)
    limiter.init_app(app)
    auth.login_manager.init_app(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(files.bp)
    app.register_blueprint(admin.bp)
    cli.register(app)

    @app.errorhandler(413)
    def too_large(_e):
        limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return jsonify(success=False, error=f"File is too large (max {limit_mb} MB)."), 413

    from .quota import QuotaError

    @app.errorhandler(QuotaError)
    def over_quota(e):
        return jsonify(success=False, error=str(e)), 507

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        return jsonify(success=False, error="Security token missing or expired. Reload the page."), 400

    @app.errorhandler(429)
    def rate_limited(_e):
        return jsonify(success=False, error="Too many requests. Slow down."), 429

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        return resp

    return app
