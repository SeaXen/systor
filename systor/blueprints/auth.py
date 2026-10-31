"""Authentication blueprint for Systor."""
from __future__ import annotations
import time
from flask import Blueprint, render_template, request, redirect, url_for, abort, session
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
    if not auth_enabled() and request.args.get("preview") != "1":
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


@auth_bp.route("/logout", methods=["GET", "POST"], endpoint="page_logout")
def page_logout():
    if not client_private():
        abort(404)
    auth_logout_session()
    if auth_enabled():
        return redirect(url_for("auth.page_login"))
    return redirect(url_for("dashboard.page_dashboard"))
