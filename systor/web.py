"""systor web dashboard — Clean Flask app on port 6677 with modular Blueprints."""
from __future__ import annotations
import logging
import os
import signal
import time  # noqa: F401
from pathlib import Path
from flask import Flask, jsonify, request, abort, redirect, url_for, session

from .config import load_config
from .services.runtime_service import start_apps_cache_worker
from .blueprints import register_blueprints
from .blueprints.common import (
    client_private, auth_cfg, auth_enabled, auth_logged_in,
    auth_session_expired, auth_logout_session, auth_touch_session,
    private_auth_protected, public_cfg, effective_private_ui,
    _login_rate_state,  # noqa: F401
)

log = logging.getLogger("systor.web")
_running = True


def _handle_term(signum, _frame):
    global _running
    log.info("web: received signal %d, shutting down", signum)
    _running = False
    raise SystemExit(0)


try:
    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGINT, _handle_term)
except Exception:
    pass


def create_app(start_worker: bool = True) -> Flask:
    """Application factory for systor web service."""
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static"
    )
    auth_boot = auth_cfg()
    app.secret_key = auth_boot.get("session_secret") or os.environ.get("SYSTOR_SESSION_SECRET") or "systor-secret-key-32b-secure-fallback"
    app.config["JSON_SORT_KEYS"] = False
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 300
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    @app.before_request
    def _csrf_protection():
        import secrets
        if "systor_csrf" not in session:
            session["systor_csrf"] = secrets.token_hex(16)
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if app.config.get("TESTING") and not app.config.get("CSRF_CHECK_IN_TESTS"):
                return None
            expected_session = session.get("systor_csrf")
            expected_cookie = request.cookies.get("systor_csrf")
            token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
            valid = False
            if token:
                if expected_cookie and secrets.compare_digest(token, expected_cookie):
                    valid = True
                elif expected_session and secrets.compare_digest(token, expected_session):
                    valid = True
            if not valid:
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "Invalid or missing CSRF token"}), 403
                abort(403)

    @app.context_processor
    def _ctx_access():
        private = client_private()
        effective_private = effective_private_ui()
        auth = auth_cfg()
        return {
            "storage_private": effective_private,
            "client_private": effective_private,
            "client_private_raw": private,
            "auth_enabled": auth_enabled(),
            "auth_mode": auth.get("mode", "admin_only"),
            "auth_username": auth.get("username", "admin"),
            "auth_logged_in": auth_logged_in(),
            "public_pages": public_cfg(),
        }

    @app.after_request

    def _add_security_headers(resp):
        path = request.path or ""
        if path.startswith("/static/"):
            resp.headers.setdefault("Cache-Control", "public, max-age=300")
        elif "Cache-Control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; img-src 'self' data:;")
        if "systor_csrf" in session and "systor_csrf" not in request.cookies:
            resp.set_cookie("systor_csrf", session["systor_csrf"], samesite="Lax")
        return resp

    @app.before_request
    def _block_public_surfaces():
        path = request.path or ""
        private = client_private()
        if private:
            if auth_enabled() and private_auth_protected(path):
                expired, timeout_min = auth_session_expired()
                if expired:
                    auth_logout_session()
                    if path.startswith("/api/"):
                        return jsonify({"ok": False, "error": f"session expired after {timeout_min}m idle"}), 401
                    next_url = request.full_path if request.query_string else request.path
                    if next_url.endswith("?"):
                        next_url = next_url[:-1]
                    return redirect(url_for("auth.page_login", next=next_url))
                if not auth_logged_in():
                    if path.startswith("/api/"):
                        return jsonify({"ok": False, "error": "login required"}), 401
                    next_url = request.full_path if request.query_string else request.path
                    if next_url.endswith("?"):
                        next_url = next_url[:-1]
                    return redirect(url_for("auth.page_login", next=next_url))
                auth_touch_session()
            return None

        if path == "/metrics":
            return None

        # Public internet: allow only explicitly enabled read-only pages.
        public_pages = public_cfg()
        public_page_map = {
            "/": public_pages.get("dashboard", True),
            "/alerts": public_pages.get("alerts", False),
        }
        public_pages_blocked = ("/apps", "/network", "/speed", "/logs", "/settings", "/storage", "/login", "/logout")
        if path in public_pages_blocked or any(path.startswith(p + "/") for p in public_pages_blocked):
            abort(404)
        if path == "/alerts" and not public_page_map.get("/alerts"):
            abort(404)

        # Public internet: allow only read-only data for enabled pages.
        public_api_allow = {
            "/health",
            "/api/version",
            "/metrics",
        }
        if public_page_map.get("/"):
            public_api_allow.update({
                "/api/snapshot",
                "/api/series",
                "/api/network-series",
                "/api/public-dashboard",
            })
        if public_page_map.get("/alerts"):
            public_api_allow.update({
                "/api/alerts",
            })
        if path.startswith("/api/") and path not in public_api_allow:
            return jsonify({"ok": False, "error": "This API is LAN/Tailscale/local only"}), 403

        if path.startswith("/storage"):
            abort(404)

    register_blueprints(app)

    # Start background cache worker when not running under unit tests
    if start_worker and not app.config.get("TESTING"):
        start_apps_cache_worker()
        from .services.uptime_service import start_uptime_worker
        start_uptime_worker()

    return app



# Backwards compatibility helpers
def _client_private() -> bool:
    return client_private()


def _auth_cfg() -> dict:
    return auth_cfg()


def _auth_enabled() -> bool:
    return auth_enabled()


def _auth_logged_in() -> bool:
    return auth_logged_in()


def run():
    cfg = load_config()
    log_cfg = cfg.get("logging", {})
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_file = log_cfg.get("file")
    if log_file:
        try:
            p = Path(log_file).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(str(p), encoding="utf-8"))
        except Exception as e:
            log.warning("web: could not open log file %s: %s", log_file, e)

    logging.basicConfig(
        level=getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )

    web_cfg = cfg.get("web", {})
    host = web_cfg.get("host", "127.0.0.1")
    port = int(web_cfg.get("port", 6677))

    log.info("web: starting on %s:%d", host, port)
    app = create_app()

    try:
        from waitress import serve
        log.info("web: using waitress with threads=16")
        serve(app, host=host, port=port, threads=16, ident="systor")
    except ImportError:
        log.info("web: waitress not installed, falling back to flask dev server")
        app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    run()
