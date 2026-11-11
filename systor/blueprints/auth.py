"""Authentication blueprint for Systor."""
from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, abort, current_app, jsonify
from werkzeug.security import check_password_hash

from .common import (
    client_private, auth_enabled, auth_logged_in, auth_cfg,
    login_rate_status, login_rate_fail, login_rate_success,
    auth_logout_session, auth_login_session
)
from ..security import get_client_ip, parse_trusted_proxies
from .common import get_trusted_proxies

auth_bp = Blueprint("auth", __name__)


def _req_ip() -> str:
    nets = parse_trusted_proxies(get_trusted_proxies())
    return get_client_ip(request, nets)


@auth_bp.route("/login", methods=["GET", "POST"], endpoint="page_login")
def page_login():
    if not client_private():
        abort(404)
    if not auth_enabled() and not current_app.config.get("AUDIT_MODE") and request.args.get("preview") != "1":
        return redirect(url_for("dashboard.page_dashboard"))
    if auth_logged_in():
        nxt = str(request.values.get("next") or "/")
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/"
        return redirect(nxt)
    error = ""
    next_url = str(request.values.get("next") or "/")
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"
    ip = _req_ip()
    blocked, wait_sec = login_rate_status(ip)
    if blocked:
        error = f"Too many failed logins. Try again in {wait_sec}s."
        status = 429 if request.method == "POST" else 200
        return render_template("login.html", error=error, next_url=next_url, wait_sec=wait_sec), status
    if request.method == "POST":
        auth = auth_cfg()
        username = str(request.form.get("username") or "").strip()
        blocked, wait_sec = login_rate_status(ip, username)
        if blocked:
            error = f"Too many failed logins. Try again in {wait_sec}s."
            return render_template("login.html", error=error, next_url=next_url, wait_sec=wait_sec), 429
        password = str(request.form.get("password") or "")
        if username == auth.get("username") and auth.get("password_hash"):
            try:
                ok = check_password_hash(auth.get("password_hash", ""), password)
            except Exception:
                ok = False
            if ok:
                login_rate_success(ip, username)
                auth_login_session(auth.get("username"))
                return redirect(next_url or "/")
        blocked, wait_sec = login_rate_fail(ip, username)
        if blocked:
            error = f"Too many failed logins. Try again in {wait_sec}s."
            return render_template("login.html", error=error, next_url=next_url, wait_sec=wait_sec), 429
        error = "Invalid username or password"
        return render_template("login.html", error=error, next_url=next_url, wait_sec=0), 401
    return render_template("login.html", error=error, next_url=next_url, wait_sec=0)


@auth_bp.route("/setup", methods=["GET", "POST"], endpoint="page_setup")
def page_setup():
    if not client_private():
        abort(404)
    if auth_enabled() and not current_app.config.get("AUDIT_MODE"):
        return redirect(url_for("auth.page_login"))
    error = ""
    next_url = str(request.values.get("next") or "/")
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"
    if request.method == "POST":
        username = str(request.form.get("username") or "").strip()
        password = str(request.form.get("password") or "")
        confirm = str(request.form.get("confirm_password") or "")
        if not username:
            error = "Username is required."
        elif len(password) < 12:
            error = "Password must be at least 12 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            from werkzeug.security import generate_password_hash
            from ..config import load_config, save_config, get_or_create_session_secret
            cfg = load_config()
            cfg.setdefault("auth", {})
            cfg["auth"]["enabled"] = True
            cfg["auth"]["username"] = username
            cfg["auth"]["password_hash"] = generate_password_hash(password)
            cfg["auth"]["session_secret"] = get_or_create_session_secret(cfg)
            try:
                save_config(cfg)
                auth_login_session(username)
                return redirect(next_url or "/")
            except Exception as e:
                error = f"Failed to save configuration: {e}"
        return render_template("setup.html", error=error, next_url=next_url, username=username), 400
    return render_template("setup.html", error=error, next_url=next_url, username="admin")


@auth_bp.route("/api/setup", methods=["POST"], endpoint="api_setup")
def api_setup():
    if not client_private():
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if auth_enabled():
        return jsonify({"ok": False, "error": "Setup already completed"}), 400
    data = request.get_json(silent=True) or request.form
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    confirm = str(data.get("confirm_password") or "")
    if not username:
        return jsonify({"ok": False, "error": "Username is required"}), 400
    if len(password) < 12:
        return jsonify({"ok": False, "error": "Password must be at least 12 characters"}), 400
    if password != confirm:
        return jsonify({"ok": False, "error": "Passwords do not match"}), 400

    from werkzeug.security import generate_password_hash
    from ..config import load_config, save_config, get_or_create_session_secret
    cfg = load_config()
    cfg.setdefault("auth", {})
    cfg["auth"]["enabled"] = True
    cfg["auth"]["username"] = username
    cfg["auth"]["password_hash"] = generate_password_hash(password)
    cfg["auth"]["session_secret"] = get_or_create_session_secret(cfg)
    try:
        save_config(cfg)
        auth_login_session(username)
        return jsonify({"ok": True, "message": "Admin credentials configured successfully", "redirect": "/"})
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed to save configuration: {e}"}), 500


@auth_bp.route("/logout", methods=["GET", "POST"], endpoint="page_logout")
def page_logout():
    if not client_private():
        abort(404)
    auth_logout_session()
    if auth_enabled():
        return redirect(url_for("auth.page_login"))
    return redirect(url_for("dashboard.page_dashboard"))
