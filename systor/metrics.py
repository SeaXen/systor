"""System metric readers. All readers are best-effort and return None on error."""
from __future__ import annotations
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


def read_loadavg() -> float | None:
    try:
        parts = Path("/proc/loadavg").read_text().split()
        return float(parts[0])
    except Exception:
        return None


def read_cpu_percent(sample_interval: float = 0.5) -> float | None:
    """CPU usage % over a short sample. Returns None on error.

    Reads /proc/stat twice and computes delta. ~sample_interval second cost.
    """
    try:
        t0 = _stat_total_idle()
        time.sleep(sample_interval)
        t1 = _stat_total_idle()
        total = t1[0] - t0[0]
        idle = t1[1] - t0[1]
        if total <= 0:
            return 0.0
        return round(100.0 * (total - idle) / total, 1)
    except Exception:
        return None


def _stat_total_idle() -> tuple[int, int]:
    line = Path("/proc/stat").read_text().splitlines()[0]  # "cpu  ..."
    vals = [int(x) for x in line.split()[1:]]
    total = sum(vals)
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
    return total, idle


def read_cpu_temp_c() -> float | None:
    """Max CPU temperature across /sys/class/thermal zones. Returns None if unavailable."""
    try:
        zones = list(Path("/sys/class/thermal").glob("thermal_zone*/temp"))
        temps: list[float] = []
        for z in zones:
            try:
                t = int(z.read_text().strip())
                if t > 0:
                    temps.append(t / 1000.0)
            except Exception:
                continue
        return max(temps) if temps else None
    except Exception:
        return None


def read_meminfo() -> dict[str, int] | None:
    """Returns dict with: total_mb, free_mb, available_mb, used_mb, swap_total_mb, swap_used_mb."""
    try:
        raw: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            if not v:
                continue
            num = v.strip().split()[0]
            try:
                raw[k] = int(num)
            except ValueError:
                pass
        if not raw:
            return None
        return {
            "total_mb": raw.get("MemTotal", 0) // 1024,
            "free_mb": raw.get("MemFree", 0) // 1024,
            "available_mb": raw.get("MemAvailable", raw.get("MemFree", 0)) // 1024,
            "used_mb": (raw.get("MemTotal", 0) - raw.get("MemAvailable", raw.get("MemFree", 0))) // 1024,
            "swap_total_mb": raw.get("SwapTotal", 0) // 1024,
            "swap_used_mb": (raw.get("SwapTotal", 0) - raw.get("SwapFree", 0)) // 1024,
        }
    except Exception:
        return None


def read_disk_usage() -> list[dict]:
    """Returns list of {mount, used_pct, size_gb, used_gb} for real filesystems."""
    try:
        out = subprocess.run(["df", "-PB", "G"], capture_output=True, text=True, timeout=5).stdout
        rows: list[dict] = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 6:
                continue
            try:
                pct = int(parts[4].rstrip("%"))
                size_gb = float(parts[1].rstrip("G"))
                used_gb = float(parts[2].rstrip("G"))
            except ValueError:
                continue
            mount = parts[5]
            # skip pseudo filesystems
            if mount.startswith(("/sys", "/proc", "/run", "/dev", "/snap", "/var/lib/docker/overlay")):
                continue
            rows.append({"mount": mount, "used_pct": pct, "size_gb": size_gb, "used_gb": used_gb})
        return rows
    except Exception:
        return []


def read_uptime_sec() -> int | None:
    try:
        return int(float(Path("/proc/uptime").read_text().split()[0]))
    except Exception:
        return None


def read_hostname() -> str:
    return os.uname().nodename if hasattr(os, "uname") else (os.environ.get("HOSTNAME") or "unknown")


def read_network_stats() -> dict[str, int] | None:
    """Bytes sent/received from /proc/net/dev. Cumulative counters."""
    try:
        result: dict[str, int] = {"rx_bytes": 0, "tx_bytes": 0}
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
            if ":" not in line:
                continue
            iface, rest = line.split(":", 1)
            iface = iface.strip()
            if iface == "lo":
                continue
            cols = rest.split()
            try:
                result["rx_bytes"] += int(cols[0])
                result["tx_bytes"] += int(cols[8])
            except (ValueError, IndexError):
                continue
        return result
    except Exception:
        return None


def collect_snapshot() -> dict[str, Any]:
    """One-time snapshot of all metrics. Returned as a single dict."""
    load = read_loadavg()
    temp = read_cpu_temp_c()
    mem = read_meminfo() or {}
    disks = read_disk_usage()
    cpu_pct = read_cpu_percent(sample_interval=0.4)
    net = read_network_stats() or {}
    return {
        "ts": int(time.time()),
        "hostname": read_hostname(),
        "uptime_sec": read_uptime_sec(),
        "cpu": {
            "load_1m": load,
            "load_5m": _safe_loadavg(1),
            "load_15m": _safe_loadavg(2),
            "temp_c": temp,
            "percent": cpu_pct,
        },
        "memory": mem,
        "disks": disks,
        "network": net,
    }


def _safe_loadavg(idx: int) -> float | None:
    try:
        parts = Path("/proc/loadavg").read_text().split()
        return float(parts[idx])
    except Exception:
        return None
