"""Runtime inspection, process metrics, and Docker/Host monitoring service."""
from __future__ import annotations
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..config import load_config
from ..metrics import read_top_processes, read_total_memory_mb
from ..storage import DEFAULT_DB_PATH

log = logging.getLogger("systor.services.runtime")

_docker_cache: dict = {"ts": 0.0, "rows": []}
_docker_cache_lock = threading.Lock()

_apps_cache: dict = {"ts": 0.0, "limit": 0, "host_rows": [], "docker_rows": []}
_apps_cache_lock = threading.Lock()

_worker_started = False
_worker_lock = threading.Lock()


def _parse_docker_size_mb(text: str) -> float:
    text = (text or "").strip()
    if not text:
        return 0.0
    m = re.match(r"([0-9.]+)\s*([kKmMgGtTpP]?i?[bB])", text)
    if not m:
        return 0.0
    num = float(m.group(1))
    unit = m.group(2)
    norm = unit.lower()
    factor = {
        "b": 1 / (1024 * 1024),
        "kib": 1 / 1024,
        "kb": 1 / 1024,
        "mib": 1,
        "mb": 1,
        "gib": 1024,
        "gb": 1024,
        "tib": 1024 * 1024,
        "tb": 1024 * 1024,
        "pib": 1024 * 1024 * 1024,
        "pb": 1024 * 1024 * 1024,
    }.get(norm, 0)
    return round(num * factor, 3)


def get_docker_apps(limit: int = 24) -> list[dict]:
    now = time.time()
    with _docker_cache_lock:
        cached = list(_docker_cache.get("rows") or [])
        cached_ts = float(_docker_cache.get("ts") or 0)
    if cached_ts > 0 and now - cached_ts < 10:
        return cached[:limit]
    try:
        if subprocess.run(["bash", "-lc", "command -v docker >/dev/null"], timeout=3).returncode != 0:
            with _docker_cache_lock:
                _docker_cache["ts"] = now
                _docker_cache["rows"] = []
            return []
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Container}}\t{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}"],
            capture_output=True,
            text=True,
            timeout=12,
        ).stdout.splitlines()
        rows: list[dict] = []
        for line in out:
            parts = line.split("\t")
            if len(parts) < 6:
                continue
            cid, name, cpu_s, mem_s, net_s, blk_s = parts[:6]
            cpu_pct = float(cpu_s.strip().rstrip("%") or 0)
            mem_used_s = mem_s.split("/")[0].strip()
            net_in_s = net_s.split("/")[0].strip()
            net_out_s = net_s.split("/")[1].strip() if "/" in net_s else "0B"
            blk_in_s = blk_s.split("/")[0].strip()
            blk_out_s = blk_s.split("/")[1].strip() if "/" in blk_s else "0B"
            rows.append({
                "kind": "docker", "id": cid[:12], "name": name,
                "cpu_percent": round(cpu_pct, 2),
                "mem_mb": _parse_docker_size_mb(mem_used_s),
                "net_in_mb": _parse_docker_size_mb(net_in_s),
                "net_out_mb": _parse_docker_size_mb(net_out_s),
                "disk_in_mb": _parse_docker_size_mb(blk_in_s),
                "disk_out_mb": _parse_docker_size_mb(blk_out_s),
            })
        with _docker_cache_lock:
            _docker_cache["ts"] = now
            _docker_cache["rows"] = rows
        return rows[:limit]
    except Exception:
        if not cached:
            with _docker_cache_lock:
                _docker_cache["ts"] = now
                _docker_cache["rows"] = []
        return cached[:limit] if cached else []


def get_host_apps(limit: int = 24) -> list[dict]:
    total_mb = max(1, read_total_memory_mb())
    seen: set[int] = set()
    rows: list[dict] = []
    for mode in ("cpu", "mem"):
        for p in read_top_processes(n=limit, by=mode):
            pid = int(p.get("pid") or 0)
            if not pid or pid in seen:
                continue
            seen.add(pid)
            rows.append({
                "kind": "host", "id": str(pid), "pid": pid,
                "name": p.get("name") or f"pid-{pid}", "user": p.get("user") or "",
                "cpu_percent": round(float(p.get("cpu_percent") or 0), 2),
                "mem_mb": round(float(p.get("mem_mb") or 0), 2),
                "mem_percent": round(100.0 * float(p.get("mem_mb") or 0) / total_mb, 2),
                "net_in_mb": 0.0, "net_out_mb": 0.0, "disk_in_mb": 0.0, "disk_out_mb": 0.0,
                "cmdline": p.get("cmdline") or "",
            })
    rows.sort(key=lambda r: (r.get("cpu_percent", 0), r.get("mem_mb", 0)), reverse=True)
    return rows[:limit]


def get_cached_apps() -> dict:
    with _apps_cache_lock:
        return dict(_apps_cache)


def set_cached_apps(ts: float, limit: int, host_rows: list[dict], docker_rows: list[dict]) -> None:
    with _apps_cache_lock:
        _apps_cache["ts"] = ts
        _apps_cache["limit"] = limit
        _apps_cache["host_rows"] = host_rows
        _apps_cache["docker_rows"] = docker_rows


def _apps_cache_refresh_worker() -> None:
    while True:
        try:
            host_all = get_host_apps(limit=96)
            docker_all = get_docker_apps(limit=96)
            set_cached_apps(time.time(), 96, host_all, docker_all)
        except Exception:
            pass
        time.sleep(4.0)


def start_apps_cache_worker() -> None:
    global _worker_started
    with _worker_lock:
        if not _worker_started:
            t = threading.Thread(target=_apps_cache_refresh_worker, name="systor-apps-cache", daemon=True)
            t.start()
            _worker_started = True


def find_exact_processes(args_variants: list[str]) -> list[dict]:
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid=,etimes=,args="],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.splitlines()
        rows: list[dict] = []
        for line in out:
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 2)
            if len(parts) != 3:
                continue
            pid_txt, etimes_txt, args = parts
            if args.strip() not in args_variants:
                continue
            try:
                rows.append({"pid": int(pid_txt), "uptime_sec": int(etimes_txt), "args": args.strip()})
            except ValueError:
                continue
        return rows
    except Exception:
        return []


def pid_stats(pid: int | None) -> dict:
    if not pid:
        return {"cpu_percent": None, "rss_mb": None, "vsz_mb": None}
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "%cpu=,rss=,vsz="], capture_output=True, text=True, timeout=3).stdout.strip()
        if not out:
            return {"cpu_percent": None, "rss_mb": None, "vsz_mb": None}
        parts = out.split()
        return {
            "cpu_percent": float(parts[0]) if len(parts) > 0 else None,
            "rss_mb": round(int(parts[1]) / 1024, 1) if len(parts) > 1 else None,
            "vsz_mb": round(int(parts[2]) / 1024, 1) if len(parts) > 2 else None,
        }
    except Exception:
        return {"cpu_percent": None, "rss_mb": None, "vsz_mb": None}


def file_size_mb(path: Path) -> float:
    try:
        return round(path.stat().st_size / (1024 * 1024), 2)
    except Exception:
        return 0.0


def systor_storage_state() -> dict:
    db = DEFAULT_DB_PATH
    logp = Path(load_config().get("logging", {}).get("file", "/var/log/systor/systor.log"))
    db_mb = file_size_mb(db)
    log_mb = file_size_mb(logp)
    return {"db_mb": db_mb, "log_mb": log_mb, "total_mb": round(db_mb + log_mb, 2), "db_path": str(db), "log_path": str(logp)}


def get_runtime_status() -> dict:
    cfg = load_config()
    py = sys.executable
    web_rows = find_exact_processes(["python3 -m systor serve web", f"{py} -m systor serve web"])
    collector_rows = find_exact_processes(["python3 -m systor serve collector", f"{py} -m systor serve collector"])

    def _proc_row(rows: list[dict]) -> dict:
        if not rows:
            return {"running": False, "pid": None, "uptime_sec": None, "cpu_percent": None, "rss_mb": None, "vsz_mb": None}
        row = rows[0]
        return {"running": True, "pid": row["pid"], "uptime_sec": row["uptime_sec"]} | pid_stats(row["pid"])

    cloudflare = {"running": False, "pid": None, "uptime_sec": None, "label": "cloudflared"}
    tailscaled = {"running": False, "pid": None, "uptime_sec": None, "label": "tailscaled"}
    try:
        out = subprocess.run(["ps", "-eo", "pid=,etimes=,comm=,args="], capture_output=True, text=True, timeout=3).stdout.splitlines()
        for line in out:
            parts = line.strip().split(None, 3)
            if len(parts) < 4:
                continue
            pid_txt, etimes_txt, comm, args = parts
            try:
                pid = int(pid_txt)
                etimes = int(etimes_txt)
            except ValueError:
                continue
            if not cloudflare["running"] and (comm == "cloudflared" or args.startswith("cloudflared ")):
                cloudflare = {"running": True, "pid": pid, "uptime_sec": etimes, "label": "cloudflared"}
            if not tailscaled["running"] and (comm == "tailscaled" or args.startswith("tailscaled ")):
                tailscaled = {"running": True, "pid": pid, "uptime_sec": etimes, "label": "tailscaled"}
    except Exception:
        pass

    return {
        "ok": True,
        "web": _proc_row(web_rows) | {"host": cfg.get("web", {}).get("host", "0.0.0.0"), "port": int(cfg.get("web", {}).get("port", 6677))},
        "collector": _proc_row(collector_rows) | {"poll_interval_sec": int(cfg.get("collector", {}).get("poll_interval_sec", 30))},
        "cloudflared": cloudflare,
        "tailscaled": tailscaled,
        "storage": systor_storage_state(),
    }
