"""Systemd service management and safe control service.

Allows inspection of system units and rate-limited, audited restarting of services
with strict injection guards and critical infrastructure deny-lists.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import re
import shutil
import subprocess
import time
from typing import Any

from ..config import load_config

log = logging.getLogger("systor.services.systemd")

# Allowed service unit name pattern
_UNIT_RE = re.compile(r"^[a-zA-Z0-9_\-\.@]+\.service$")

_last_restart_by_ip: dict[str, float] = {}


def get_allowed_units() -> list[str]:
    """Retrieve allowed systemd unit globs from configuration."""
    cfg = load_config()
    allowed = cfg.get("systemd", {}).get("allowed_units")
    if isinstance(allowed, str):
        if allowed.startswith("[") and allowed.endswith("]"):
            try:
                import ast
                parsed = ast.literal_eval(allowed)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except Exception:
                pass
        return [x.strip() for x in allowed.split(",") if x.strip()]
    if isinstance(allowed, list):
        return [str(x).strip() for x in allowed if str(x).strip()]
    return ["systor-*.service"]


def is_unit_allowed(unit_name: str, allowed_patterns: list[str] | None = None) -> bool:
    """Check if a systemd unit name matches any allowed unit glob pattern."""
    if allowed_patterns is None:
        allowed_patterns = get_allowed_units()
    clean = unit_name.strip().lower()
    return any(fnmatch.fnmatch(clean, pat.strip().lower()) for pat in allowed_patterns)


def list_systemd_services() -> dict[str, Any]:
    """List systemd services on host."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return {
            "available": False,
            "services": [],
            "error": "systemctl not found (not a systemd host or running inside minimal container)"
        }

    try:
        # Try JSON output format first (systemd 240+)
        p = subprocess.run(
            [systemctl, "list-units", "--type=service", "--all", "--no-pager", "--output=json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if p.returncode == 0 and p.stdout:
            raw_units = json.loads(p.stdout)
            units = []
            for u in raw_units:
                unit_name = str(u.get("unit") or "")
                units.append({
                    "unit": unit_name,
                    "load": u.get("load"),
                    "active": u.get("active"),
                    "sub": u.get("sub"),
                    "description": u.get("description", ""),
                    "actionable": is_unit_allowed(unit_name),
                })
            return {"available": True, "services": units, "count": len(units)}
    except Exception:
        pass

    # Fallback to standard tabular list-units parsing
    try:
        p = subprocess.run(
            [systemctl, "list-units", "--type=service", "--all", "--no-pager", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        units = []
        for line in p.stdout.splitlines():
            parts = line.split(None, 4)
            if len(parts) >= 4 and parts[0].endswith(".service"):
                unit_name = parts[0]
                units.append({
                    "unit": unit_name,
                    "load": parts[1],
                    "active": parts[2],
                    "sub": parts[3],
                    "description": parts[4] if len(parts) > 4 else "",
                    "actionable": is_unit_allowed(unit_name),
                })
        return {"available": True, "services": units, "count": len(units)}
    except Exception as e:
        return {"available": False, "services": [], "error": str(e)}


def restart_systemd_service(unit: str, user: str, ip: str, storage) -> tuple[bool, str]:
    """Safely restart a validated systemd service with audit logging."""
    clean_unit = unit.strip()

    if not _UNIT_RE.match(clean_unit):
        msg = f"Invalid service unit name: '{clean_unit}'"
        storage.log_systemd_action(clean_unit, "restart", user, ip, False, msg)
        return False, msg

    # S06: Enforce allow-list (systemd.allowed_units)
    if not is_unit_allowed(clean_unit):
        msg = f"Operation rejected: '{clean_unit}' is not in systemd.allowed_units allow-list."
        storage.log_systemd_action(clean_unit, "restart", user, ip, False, msg)
        return False, msg

    now = time.time()
    last = _last_restart_by_ip.get(ip, 0.0)
    if (now - last) < 10.0:
        msg = f"Rate limit exceeded: please wait {round(10.0 - (now - last), 1)}s before restarting another service."
        storage.log_systemd_action(clean_unit, "restart", user, ip, False, msg)
        return False, msg
    _last_restart_by_ip[ip] = now

    systemctl = shutil.which("systemctl")
    if not systemctl:
        msg = "systemctl command not available on host."
        storage.log_systemd_action(clean_unit, "restart", user, ip, False, msg)
        return False, msg

    try:
        proc = subprocess.run(
            [systemctl, "restart", clean_unit],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            msg = f"Successfully restarted {clean_unit}"
            storage.log_systemd_action(clean_unit, "restart", user, ip, True, msg)
            return True, msg
        else:
            err = proc.stderr.strip() or f"Exited with code {proc.returncode}"
            msg = f"Failed to restart {clean_unit}: {err}"
            storage.log_systemd_action(clean_unit, "restart", user, ip, False, msg)
            return False, msg
    except subprocess.TimeoutExpired:
        msg = f"Timeout while restarting {clean_unit}"
        storage.log_systemd_action(clean_unit, "restart", user, ip, False, msg)
        return False, msg
    except Exception as e:
        msg = f"Error restarting {clean_unit}: {e}"
        storage.log_systemd_action(clean_unit, "restart", user, ip, False, msg)
        return False, msg
