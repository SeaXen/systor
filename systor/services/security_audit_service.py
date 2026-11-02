"""Security & Network Audit Service for Systor.

Inspects open listening ports (via ss / socket table), UFW firewall status,
and Fail2ban active jails with zero external dependencies and safe caching.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from typing import Any

log = logging.getLogger("systor.services.security_audit")

_AUDIT_CACHE: dict[str, Any] = {
    "ts": 0.0,
    "data": {
        "listening_ports": [],
        "firewall": {"active": False, "rules_count": 0, "rules": []},
        "fail2ban": {"active": False, "jails_count": 0, "jails": [], "banned_total": 0},
    },
}
_CACHE_TTL = 15.0


def parse_listening_ports_output(stdout: str) -> list[dict[str, Any]]:
    """Parse output from `ss -tulpn` or `netstat -tulpn`."""
    ports: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("Netid") or line.startswith("Active") or line.startswith("Proto"):
            continue

        parts = line.split()
        if len(parts) < 4:
            continue

        proto = "TCP"
        local_addr = ""
        user_field = ""

        # Check if first col is protocol
        first = parts[0].lower()
        if "tcp" in first:
            proto = "TCP"
            local_addr = parts[4] if len(parts) >= 5 else parts[3]
            user_field = parts[6] if len(parts) >= 7 else (parts[5] if len(parts) >= 6 else "")
        elif "udp" in first:
            proto = "UDP"
            local_addr = parts[4] if len(parts) >= 5 else parts[3]
            user_field = parts[6] if len(parts) >= 7 else (parts[5] if len(parts) >= 6 else "")
        elif first == "listen":
            proto = "TCP"
            local_addr = parts[3] if len(parts) >= 4 else ""
            user_field = parts[5] if len(parts) >= 6 else ""
        elif first == "unconn":
            proto = "UDP"
            local_addr = parts[3] if len(parts) >= 4 else ""
            user_field = parts[5] if len(parts) >= 6 else ""
        else:
            # Search for the address containing ':'
            for idx, p in enumerate(parts):
                if ":" in p and any(c.isdigit() for c in p):
                    local_addr = p
                    user_field = parts[-1] if idx < len(parts) - 1 else ""
                    break

        if not local_addr or ":" not in local_addr:
            continue

        host_str, _, port_str = local_addr.rpartition(":")
        if not port_str.isdigit():
            continue
        port_num = int(port_str)

        proc_info = ""
        pid = None
        if user_field:
            m = re.search(r'"([^"]+)",pid=(\d+)', user_field)
            if m:
                proc_info = m.group(1)
                pid = int(m.group(2))
            else:
                proc_info = user_field.strip()

        key = (proto, port_num)
        if key in seen:
            continue
        seen.add(key)

        ports.append({
            "proto": proto,
            "port": port_num,
            "address": host_str or "*",
            "process": proc_info or "—",
            "pid": pid,
        })

    ports.sort(key=lambda x: x["port"])
    return ports


def get_listening_ports() -> list[dict[str, Any]]:
    """Fetch listening TCP/UDP ports via ss or netstat."""
    ss_bin = shutil.which("ss")
    if ss_bin:
        try:
            p = subprocess.run([ss_bin, "-tulpn", "-H"], capture_output=True, text=True, timeout=3)
            if p.returncode == 0 and p.stdout:
                return parse_listening_ports_output(p.stdout)
        except Exception as e:
            log.debug("ss -tulpn failed: %s", e)

    netstat_bin = shutil.which("netstat")
    if netstat_bin:
        try:
            p = subprocess.run([netstat_bin, "-tulpn"], capture_output=True, text=True, timeout=3)
            if p.returncode == 0 and p.stdout:
                return parse_listening_ports_output(p.stdout)
        except Exception as e:
            log.debug("netstat -tulpn failed: %s", e)

    return []


def parse_ufw_output(stdout: str) -> dict[str, Any]:
    """Parse `ufw status verbose` text."""
    active = "Status: active" in stdout
    rules: list[str] = []
    in_rules = False

    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("--"):
            in_rules = True
            continue
        if in_rules and line:
            rules.append(line)

    return {
        "active": active,
        "rules_count": len(rules),
        "rules": rules[:50],
    }


def get_firewall_status() -> dict[str, Any]:
    """Inspect UFW firewall status."""
    ufw_bin = shutil.which("ufw")
    if not ufw_bin:
        return {"active": False, "rules_count": 0, "rules": [], "available": False}

    try:
        p = subprocess.run([ufw_bin, "status", "verbose"], capture_output=True, text=True, timeout=3)
        res = parse_ufw_output(p.stdout)
        res["available"] = True
        return res
    except Exception as e:
        log.debug("ufw status failed: %s", e)
        return {"active": False, "rules_count": 0, "rules": [], "available": True}


def parse_fail2ban_output(status_stdout: str) -> dict[str, Any]:
    """Parse `fail2ban-client status` text."""
    jails: list[str] = []
    for line in status_stdout.splitlines():
        if "Jail list:" in line:
            _, _, jail_str = line.partition("Jail list:")
            jails = [j.strip() for j in jail_str.split(",") if j.strip()]

    return {
        "active": True,
        "jails_count": len(jails),
        "jails": jails,
        "banned_total": 0,
    }


def get_fail2ban_status() -> dict[str, Any]:
    """Inspect Fail2ban active jails and banned counts."""
    f2b_bin = shutil.which("fail2ban-client")
    if not f2b_bin:
        return {"active": False, "jails_count": 0, "jails": [], "banned_total": 0, "available": False}

    try:
        p = subprocess.run([f2b_bin, "status"], capture_output=True, text=True, timeout=3)
        if p.returncode != 0:
            return {"active": False, "jails_count": 0, "jails": [], "banned_total": 0, "available": True}

        res = parse_fail2ban_output(p.stdout)
        res["available"] = True

        # Query banned count for each jail if possible
        total_banned = 0
        for jail in res["jails"][:10]:
            try:
                pj = subprocess.run([f2b_bin, "status", jail], capture_output=True, text=True, timeout=2)
                for line in pj.stdout.splitlines():
                    if "Total banned:" in line or "Currently banned:" in line:
                        num = re.findall(r"\d+", line)
                        if num:
                            total_banned += int(num[-1])
            except Exception:
                pass
        res["banned_total"] = total_banned
        return res
    except Exception as e:
        log.debug("fail2ban-client status failed: %s", e)
        return {"active": False, "jails_count": 0, "jails": [], "banned_total": 0, "available": True}


def get_security_audit(force_refresh: bool = False) -> dict[str, Any]:
    """Return aggregated security audit with in-memory caching."""
    now = time.time()
    if not force_refresh and (now - _AUDIT_CACHE["ts"]) < _CACHE_TTL:
        return _AUDIT_CACHE["data"]

    ports = get_listening_ports()
    fw = get_firewall_status()
    f2b = get_fail2ban_status()

    data = {
        "listening_ports": ports,
        "firewall": fw,
        "fail2ban": f2b,
        "ts": int(now),
    }

    _AUDIT_CACHE["ts"] = now
    _AUDIT_CACHE["data"] = data
    return data
