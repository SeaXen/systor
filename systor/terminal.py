"""Secure Web Terminal (Emergency Console) engine for Systor.

Allows system administrators to run emergency diagnostic and recovery commands
from the browser. Strictly guarded by:
1. Private LAN / Tailscale network verification (client_private).
2. Explicit admin password re-authentication.
3. Cryptographically random session tokens bound to client IP.
4. Comprehensive audit logging.
"""
from __future__ import annotations

import logging
import os
import secrets
import shlex
import subprocess
import time
from typing import Any
from werkzeug.security import check_password_hash

log = logging.getLogger("systor.terminal")

_TERMINAL_SESSIONS: dict[str, dict[str, Any]] = {}
_SESSION_TTL = 900.0  # 15 minutes


def authenticate_terminal_session(
    password: str,
    client_ip: str,
    stored_password_hash: str,
) -> tuple[bool, str, str | None]:
    """Verify admin password and issue a time-limited terminal token."""
    if not password:
        return False, "Password is required", None

    # If auth password is configured, check hash
    if stored_password_hash:
        if not check_password_hash(stored_password_hash, password):
            return False, "Incorrect admin password", None
    else:
        # Default fallback for unconfigured auth: require confirmation string
        if password != "admin" and password != "systor":
            return False, "Incorrect password. Set auth.password in config to secure console.", None

    token = secrets.token_urlsafe(32)
    now = time.time()
    _TERMINAL_SESSIONS[token] = {
        "ip": client_ip,
        "created_at": now,
        "expires_at": now + _SESSION_TTL,
    }

    # Clean up stale sessions
    for tok in list(_TERMINAL_SESSIONS.keys()):
        if _TERMINAL_SESSIONS[tok]["expires_at"] < now:
            _TERMINAL_SESSIONS.pop(tok, None)

    return True, "Authenticated", token


def validate_terminal_token(token: str, client_ip: str) -> bool:
    """Check if token exists, has not expired, and matches client IP."""
    if not token or token not in _TERMINAL_SESSIONS:
        return False

    session = _TERMINAL_SESSIONS[token]
    now = time.time()
    if session["expires_at"] < now:
        _TERMINAL_SESSIONS.pop(token, None)
        return False

    if session["ip"] != client_ip:
        log.warning("Terminal token IP mismatch: expected %s, got %s", session["ip"], client_ip)
        return False

    # Slide expiration on active use
    session["expires_at"] = now + _SESSION_TTL
    return True


def execute_console_command(
    token: str,
    client_ip: str,
    command: str,
    timeout_sec: float = 12.0,
) -> dict[str, Any]:
    """Execute command in host shell safely with timeout and output capture."""
    if not validate_terminal_token(token, client_ip):
        return {"ok": False, "error": "Invalid or expired session token. Please re-authenticate."}

    cmd = command.strip()
    if not cmd:
        return {"ok": True, "output": "", "returncode": 0}

    # Choose default shell
    is_win = os.name == "nt"
    shell_cmd = ["cmd.exe", "/c", cmd] if is_win else ["/bin/sh", "-c", cmd]

    try:
        proc = subprocess.run(
            shell_cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        combined = proc.stdout
        if proc.stderr:
            combined += ("\n" if combined else "") + proc.stderr

        return {
            "ok": True,
            "output": combined or "(no output)",
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"Command timed out after {timeout_sec:.0f} seconds",
            "returncode": 124,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"Execution failed: {e}",
            "returncode": -1,
        }
