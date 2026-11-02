"""Shared middleware, auth, rate-limiting, and storage accessors for Systor Blueprints."""
from __future__ import annotations
import logging
import threading
import time
from flask import request, session

from ..config import load_config
from ..storage import Storage, DEFAULT_DB_PATH
from ..security import is_request_private

log = logging.getLogger("systor.blueprints.common")

_storage: Storage | None = None
_storage_lock = threading.Lock()

_login_rate_lock = threading.Lock()
_login_rate_state: dict[str, dict] = {}
_LOGIN_MAX_FAILS = 5
_LOGIN_COOLDOWN_SEC = 60


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        with _storage_lock:
            if _storage is None:
                cfg = load_config()
                _storage = Storage(
                    db_path=DEFAULT_DB_PATH,
                    retention_days=cfg.get("collector", {}).get("retention_days", 7),
                    rollup_retention_days=cfg.get("collector", {}).get("rollup_retention_days", 90),
                )
    return _storage


def get_trusted_proxies() -> str | list[str]:
    cfg = load_config()
    return cfg.get("web", {}).get("trusted_proxies", "127.0.0.1, ::1")


def client_private() -> bool:
    """Evaluate if current request is from a private/LAN/Tailscale client.

    Strictly protects against X-Forwarded-For forgery using configured trusted proxies.
    """
    return is_request_private(request, get_trusted_proxies())


def auth_cfg() -> dict:
    cfg = load_config().get("auth", {}) or {}
    mode = str(cfg.get("mode") or "admin_only").strip().lower()
    if mode not in ("admin_only", "full_app"):
        mode = "admin_only"
    username = str(cfg.get("username") or "admin").strip() or "admin"
    try:
        idle_timeout_min = max(0, int(float(cfg.get("idle_timeout_min", 0) or 0)))
    except Exception:
        idle_timeout_min = 0
    try:
        max_fails = max(2, min(20, int(float(cfg.get("max_fails", _LOGIN_MAX_FAILS) or _LOGIN_MAX_FAILS))))
    except Exception:
        max_fails = _LOGIN_MAX_FAILS
    try:
        cooldown_sec = max(10, min(3600, int(float(cfg.get("cooldown_sec", _LOGIN_COOLDOWN_SEC) or _LOGIN_COOLDOWN_SEC))))
    except Exception:
        cooldown_sec = _LOGIN_COOLDOWN_SEC
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "mode": mode,
        "username": username,
        "password_hash": str(cfg.get("password_hash") or ""),
        "session_secret": str(cfg.get("session_secret") or ""),
        "idle_timeout_min": idle_timeout_min,
        "max_fails": max_fails,
        "cooldown_sec": cooldown_sec,
    }


def auth_enabled() -> bool:
    cfg = auth_cfg()
    return bool(cfg.get("enabled") and cfg.get("username") and cfg.get("password_hash"))


def auth_logged_in() -> bool:
    cfg = auth_cfg()
    return bool(session.get("systor_auth") and session.get("systor_user") == cfg.get("username"))


def auth_touch_session() -> None:
    if not auth_logged_in():
        return
    session.permanent = True
    session["systor_last_seen"] = int(time.time())


def auth_logout_session() -> None:
    session.pop("systor_auth", None)
    session.pop("systor_user", None)
    session.pop("systor_last_seen", None)


def auth_login_session(username: str) -> None:
    session.permanent = True
    session["systor_auth"] = True
    session["systor_user"] = username
    session["systor_last_seen"] = int(time.time())


def auth_session_expired() -> tuple[bool, int]:
    if not auth_logged_in():
        return False, 0
    timeout_min = int(auth_cfg().get("idle_timeout_min") or 0)
    if timeout_min <= 0:
        return False, 0
    now = int(time.time())
    last_seen = int(session.get("systor_last_seen") or 0)
    if last_seen and now - last_seen > timeout_min * 60:
        return True, timeout_min
    return False, timeout_min


def login_rate_key(scope: str, value: str) -> str:
    return f"{scope}:{value.strip().lower() if scope == 'user' else value.strip()}"


def login_rate_status(ip: str, username: str = "") -> tuple[bool, int]:
    now = time.time()
    keys = [login_rate_key("ip", ip)]
    if username:
        keys.append(login_rate_key("user", username))
    with _login_rate_lock:
        best_wait = 0
        active = False
        for key in keys:
            row = _login_rate_state.get(key)
            if not row:
                continue
            blocked_until = float(row.get("blocked_until") or 0)
            if blocked_until > now:
                active = True
                best_wait = max(best_wait, max(1, int(blocked_until - now)))
            elif blocked_until:
                row["blocked_until"] = 0.0
        return active, best_wait


def login_rate_fail(ip: str, username: str = "") -> tuple[bool, int]:
    now = time.time()
    auth = auth_cfg()
    max_fails = int(auth.get("max_fails") or _LOGIN_MAX_FAILS)
    cooldown_sec = int(auth.get("cooldown_sec") or _LOGIN_COOLDOWN_SEC)
    keys = [login_rate_key("ip", ip)]
    if username:
        keys.append(login_rate_key("user", username))
    with _login_rate_lock:
        best_wait = 0
        blocked = False
        for key in keys:
            row = _login_rate_state.setdefault(key, {"fails": 0, "blocked_until": 0.0})
            blocked_until = float(row.get("blocked_until") or 0)
            if blocked_until > now:
                blocked = True
                best_wait = max(best_wait, max(1, int(blocked_until - now)))
                continue
            fails = int(row.get("fails") or 0) + 1
            row["fails"] = fails
            if fails >= max_fails:
                row["fails"] = 0
                row["blocked_until"] = now + cooldown_sec
                blocked = True
                best_wait = max(best_wait, cooldown_sec)
        return blocked, best_wait


def login_rate_success(ip: str, username: str = "") -> None:
    keys = [login_rate_key("ip", ip)]
    if username:
        keys.append(login_rate_key("user", username))
    with _login_rate_lock:
        for key in keys:
            _login_rate_state.pop(key, None)


def private_auth_exempt(path: str) -> bool:
    if path.startswith("/static/"):
        return True
    return path in ("/login", "/logout", "/health", "/api/version")


def private_auth_protected(path: str) -> bool:
    if private_auth_exempt(path):
        return False
    mode = auth_cfg().get("mode", "admin_only")
    if mode == "full_app":
        return True
    admin_pages = ("/apps", "/network", "/speed", "/alerts", "/logs", "/settings", "/storage")
    if path in admin_pages or any(path.startswith(p + "/") for p in admin_pages):
        return True
    if path.startswith("/api/"):
        allow = {"/api/snapshot", "/api/series", "/api/network-series", "/api/public-dashboard", "/api/version"}
        return path not in allow
    return False


def effective_private_ui() -> bool:
    private = client_private()
    if not private:
        return False
    if not auth_enabled():
        return True
    return auth_logged_in()


def public_cfg() -> dict:
    cfg = load_config().get("public", {}) or {}
    return {
        "dashboard": bool(cfg.get("dashboard", True)),
        "alerts": bool(cfg.get("alerts", False)),
    }


def storage_can_write() -> bool:
    return client_private() and (not auth_enabled() or auth_logged_in())


def parse_hours_arg(value, default: float) -> float:
    if value in (None, "", "all"):
        return 24.0 * 3650
    try:
        val = float(value)
        return max(0.1, min(24.0 * 3650, val))
    except (ValueError, TypeError):
        return default


def bucket_series_points(rows, limit: int = 600):
    rows = list(rows or [])
    n = len(rows)
    if n <= limit:
        return rows
    first_ts, last_ts = int(rows[0][0]), int(rows[-1][0])
    span = max(1, last_ts - first_ts)
    bucket_sec = max(1, int((span + limit - 1) // limit))
    out = []
    cur_key = None
    cur_vals = []
    cur_ts = first_ts
    for ts, val in rows:
        key = int(ts) // bucket_sec
        if cur_key is None:
            cur_key = key
        if key != cur_key and cur_vals:
            out.append((cur_ts, round(sum(cur_vals) / len(cur_vals), 3)))
            cur_vals = []
            cur_key = key
        cur_ts = int(ts)
        if val is not None:
            cur_vals.append(float(val))
    if cur_vals:
        out.append((cur_ts, round(sum(cur_vals) / len(cur_vals), 3)))
    if out and out[-1][0] != last_ts:
        out.append(rows[-1])
    return out[-limit:]


def bucket_network_points(rows, limit: int = 600):
    rows = list(rows or [])
    n = len(rows)
    if n <= limit:
        return rows
    first_ts, last_ts = int(rows[0]["ts"]), int(rows[-1]["ts"])
    span = max(1, last_ts - first_ts)
    bucket_sec = max(1, int((span + limit - 1) // limit))
    out = []
    cur_key = None
    rx_vals = []
    tx_vals = []
    cur_ts = first_ts
    for row in rows:
        ts = int(row["ts"])
        key = ts // bucket_sec
        if cur_key is None:
            cur_key = key
        if key != cur_key and (rx_vals or tx_vals):
            out.append({"ts": cur_ts, "rx_mbps": round(sum(rx_vals) / len(rx_vals), 4) if rx_vals else 0.0, "tx_mbps": round(sum(tx_vals) / len(tx_vals), 4) if tx_vals else 0.0})
            rx_vals = []
            tx_vals = []
            cur_key = key
        cur_ts = ts
        rx_vals.append(float(row.get("rx_mbps") or 0.0))
        tx_vals.append(float(row.get("tx_mbps") or 0.0))
    if rx_vals or tx_vals:
        out.append({"ts": cur_ts, "rx_mbps": round(sum(rx_vals) / len(rx_vals), 4) if rx_vals else 0.0, "tx_mbps": round(sum(tx_vals) / len(tx_vals), 4) if tx_vals else 0.0})
    if out and out[-1]["ts"] != last_ts:
        out.append(rows[-1])
    return out[-limit:]

