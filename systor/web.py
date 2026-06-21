"""systor web dashboard — Flask app on port 6677.

Pages:
  /              — live dashboard (auto-refresh, dark theme)
  /api/snapshot  — current metrics JSON
  /api/series    — historical time-series JSON
  /api/alerts    — recent alerts JSON
  /api/notifications — recent notification log
  /api/system    — full system snapshot
  /settings      — web UI to edit thresholds (with duration in minutes), telegram, discord
  /logs          — recent log lines (read from log file)
  /health        — simple liveness probe
"""
from __future__ import annotations
import json
import logging
import os
import re
import signal
import shutil
import sys
import threading
import time
import subprocess
import ipaddress
import zipfile
import tempfile
import hashlib
import secrets
from datetime import datetime
from functools import wraps
from pathlib import Path

from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from flask import Flask, jsonify, render_template, request, abort, Response, send_file, after_this_request, session, redirect, url_for

from .config import load_config, save_config
from .metrics import collect_snapshot, read_top_processes, read_total_memory_mb, read_network_interfaces
from .notifier import Notifier, send_telegram, send_discord
from .storage import Storage, DEFAULT_DB_PATH
from .speed import run_provider, run_many, iperf_status, start_iperf_server, local_ipv4s, list_ookla_servers, list_librespeed_servers

log = logging.getLogger("systor.web")
_running = True


def _handle_term(signum, _frame):
    global _running
    log.info("web: received signal %d, shutting down", signum)
    _running = False
    raise SystemExit(0)


signal.signal(signal.SIGTERM, _handle_term)
signal.signal(signal.SIGINT, _handle_term)


# Cached storage (lazy-init so we don't block on slow disks)
_storage: Storage | None = None
_storage_lock = threading.Lock()

# Docker stats is the slowest Apps source. Cache briefly so 1s/3s UI refreshes
# do not hammer dockerd/containerd while still keeping the page live.
_docker_cache: dict = {"ts": 0.0, "rows": []}
_docker_cache_lock = threading.Lock()

# Cache one combined Apps snapshot briefly so the UI can derive CPU/RAM/Disk/Net
# views from a single stable sample instead of recomputing host/docker reads on
# every request burst. Short TTL keeps it feeling live while smoothing cost.
_apps_cache: dict = {"ts": 0.0, "limit": 0, "host_rows": [], "docker_rows": []}
_apps_cache_lock = threading.Lock()
_public_dash_cache: dict = {"ts": 0.0, "hours": None, "body": b"", "etag": ""}
_public_dash_cache_lock = threading.Lock()
_login_rate_lock = threading.Lock()
_login_rate_state: dict[str, dict] = {}
_LOGIN_MAX_FAILS = 5
_LOGIN_COOLDOWN_SEC = 60


def _apps_cache_refresh_worker() -> None:
    """Background refresher: pre-warms the apps cache so /api/apps never blocks
    on psutil/docker cold starts. Runs forever; daemon thread."""
    while True:
        try:
            host_all = _host_apps(limit=96)
            docker_all = _docker_apps(limit=96)
            with _apps_cache_lock:
                _apps_cache["ts"] = time.time()
                _apps_cache["limit"] = 96
                _apps_cache["host_rows"] = host_all
                _apps_cache["docker_rows"] = docker_all
        except Exception:
            pass
        time.sleep(4.0)


# Start the background refresher once on import. Daemon thread dies with the process.
try:
    threading.Thread(target=_apps_cache_refresh_worker, name="systor-apps-cache", daemon=True).start()
except Exception:
    pass

_speed_live_lock = threading.Lock()
_speed_live_proc: subprocess.Popen | None = None
_speed_live_state: dict = {
    "running": False,
    "phase": "idle",
    "progress": 0,
    "target": "",
    "server_id": "",
    "ping_ms": None,
    "jitter_ms": None,
    "packet_loss": None,
    "dl_mbps": None,
    "ul_mbps": None,
    "ok": None,
    "status": "idle",
    "cancel_requested": False,
    "result_url": "",
    "run_type": "manual",
    "started_ts": 0,
    "ended_ts": 0,
}


def _speed_live_copy() -> dict:
    with _speed_live_lock:
        return dict(_speed_live_state)


def _speed_live_update(**kwargs):
    with _speed_live_lock:
        _speed_live_state.update(kwargs)


def _speed_live_reset(server_id: str = "", target: str = "", run_type: str = "manual"):
    with _speed_live_lock:
        _speed_live_state.clear()
        _speed_live_state.update({
            "running": False,
            "phase": "idle",
            "progress": 0,
            "target": target or "",
            "server_id": str(server_id or ""),
            "ping_ms": None,
            "jitter_ms": None,
            "packet_loss": None,
            "dl_mbps": None,
            "ul_mbps": None,
            "ok": None,
            "status": "idle",
            "cancel_requested": False,
            "result_url": "",
            "run_type": run_type or "manual",
            "started_ts": 0,
            "ended_ts": 0,
        })


def _speed_live_env() -> dict:
    env = dict(os.environ)
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin")
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("LC_ALL", "C.UTF-8")
    env.setdefault("HOME", "/root")
    return env


def _speed_live_parse_line(line: str):
    line = (line or "").strip()
    if not line:
        return
    m = re.search(r"Server:\s+(.+?)\s+\(id:\s*(\d+)\)", line)
    if m:
        _speed_live_update(target=m.group(1).replace(" - ", " · "), server_id=m.group(2))
        return
    m = re.search(r"Idle Latency:\s*([0-9.]+)\s*ms\s*\(jitter:\s*([0-9.]+)ms", line)
    if m:
        _speed_live_update(phase="ping", progress=25, ping_ms=float(m.group(1)), jitter_ms=float(m.group(2)), status="ping complete")
        return
    m = re.search(r"Download:\s+([0-9.]+)\s+Mbps.*?(\d+)%", line)
    if m:
        _speed_live_update(phase="download", progress=75, dl_mbps=float(m.group(1)), status="download running")
        return
    m = re.search(r"Upload:\s+([0-9.]+)\s+Mbps.*?(\d+)%", line)
    if m:
        _speed_live_update(phase="upload", progress=75, ul_mbps=float(m.group(1)), status="upload running")
        return
    m = re.search(r"Download:\s+([0-9.]+)\s+Mbps\s+\(data used:", line)
    if m:
        _speed_live_update(phase="download", progress=75, dl_mbps=float(m.group(1)), status="download complete")
        return
    m = re.search(r"Upload:\s+([0-9.]+)\s+Mbps\s+\(data used:", line)
    if m:
        _speed_live_update(phase="upload", progress=75, ul_mbps=float(m.group(1)), status="upload complete")
        return
    m = re.search(r"Packet Loss:\s*([0-9.]+)%", line)
    if m:
        _speed_live_update(packet_loss=float(m.group(1)))
        return
    m = re.search(r"Result URL:\s*(https?://\S+)", line)
    if m:
        _speed_live_update(result_url=m.group(1))


def _speed_live_finalize(returncode: int):
    global _speed_live_proc
    snap = _speed_live_copy()
    cancelled = bool(snap.get("cancel_requested"))
    ok = (returncode == 0) and not cancelled
    if ok:
        row = {
            "ts": int(snap.get("started_ts") or time.time()),
            "provider": "ookla",
            "mode": "wan",
            "target": snap.get("target") or f"server {snap.get('server_id') or 'auto'}",
            "server_id": snap.get("server_id") or "",
            "run_type": snap.get("run_type") or "manual",
            "ping_ms": snap.get("ping_ms"),
            "jitter_ms": snap.get("jitter_ms"),
            "packet_loss": snap.get("packet_loss"),
            "dl_mbps": snap.get("dl_mbps"),
            "ul_mbps": snap.get("ul_mbps"),
            "ok": True,
            "note": snap.get("result_url") or "live ookla run",
            "raw_json": json.dumps(snap)[:16000],
        }
        get_storage().log_speedtest(row)
        _speed_live_update(ok=True, running=False, phase="complete", progress=100, status="done · results updated", ended_ts=int(time.time()))
    elif cancelled:
        _speed_live_update(ok=False, running=False, phase="stopped", progress=0, status="stopped", ended_ts=int(time.time()))
    else:
        _speed_live_update(ok=False, running=False, phase="failed", progress=100, status=f"failed · exit {returncode}", ended_ts=int(time.time()))
    with _speed_live_lock:
        _speed_live_proc = None


def _speed_live_worker(server_id: str, run_type: str = "manual"):
    global _speed_live_proc
    sid = re.sub(r"[^0-9]", "", str(server_id or ""))
    cmd = ["speedtest", "--accept-license", "--accept-gdpr"]
    if sid:
        cmd += ["-s", sid]
    pretty_target = f"server {sid}" if sid else "auto"
    _speed_live_update(running=True, phase="starting", progress=6, status="starting…", server_id=sid, target=pretty_target, run_type=run_type or "manual", started_ts=int(time.time()))
    if not shutil.which("speedtest"):
        _speed_live_update(running=False, ok=False, phase="failed", progress=0, status="failed · Ookla CLI not installed", ended_ts=int(time.time()))
        return
    wrapped = ["script", "-q", "-c", " ".join(cmd), "/dev/null"]
    proc = subprocess.Popen(wrapped, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=_speed_live_env(), bufsize=1)
    with _speed_live_lock:
        _speed_live_proc = proc
    buf = ""
    try:
        while True:
            ch = proc.stdout.read(1) if proc.stdout else ""
            if ch == "" and proc.poll() is not None:
                break
            if not ch:
                continue
            if ch in "\r\n":
                if buf.strip():
                    _speed_live_parse_line(buf)
                buf = ""
            else:
                buf += ch
        if buf.strip():
            _speed_live_parse_line(buf)
        rc = proc.wait(timeout=5)
    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        _speed_live_update(ok=False, running=False, phase="failed", progress=100, status=f"failed · {e}", ended_ts=int(time.time()))
        with _speed_live_lock:
            _speed_live_proc = None
        return
    _speed_live_finalize(rc)


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        with _storage_lock:
            if _storage is None:
                cfg = load_config()
                _storage = Storage(
                    db_path=DEFAULT_DB_PATH,
                    retention_days=cfg["collector"]["retention_days"],
                    rollup_retention_days=cfg["collector"]["rollup_retention_days"],
                )
    return _storage


def _parse_hours_arg(value, default: float) -> float:
    if value in (None, '', 'all'):
        return 24.0 * 3650
    try:
        hours = float(value)
    except (TypeError, ValueError):
        hours = float(default)
    return max(0.25, min(24.0 * 3650, hours))


def _bucket_series_points(rows, limit: int = 600):
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
    if out[-1][0] != last_ts:
        out.append(rows[-1])
    return out[-limit:]


def _bucket_network_points(rows, limit: int = 600):
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
    if out[-1]["ts"] != last_ts:
        out.append(rows[-1])
    return out[-limit:]


def _find_exact_processes(args_variants: list[str]) -> list[dict]:
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


def _pid_stats(pid: int | None) -> dict:
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


def _file_size_mb(path: Path) -> float:
    try:
        return round(path.stat().st_size / (1024 * 1024), 2)
    except Exception:
        return 0.0


def _systor_storage_state() -> dict:
    db = DEFAULT_DB_PATH
    logp = Path(load_config().get("logging", {}).get("file", "/var/log/systor/systor.log"))
    db_mb = _file_size_mb(db)
    log_mb = _file_size_mb(logp)
    return {"db_mb": db_mb, "log_mb": log_mb, "total_mb": round(db_mb + log_mb, 2), "db_path": str(db), "log_path": str(logp)}


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


def _docker_apps(limit: int = 24) -> list[dict]:
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
        out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Container}}\t{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}"], capture_output=True, text=True, timeout=12).stdout.splitlines()
        rows: list[dict] = []
        for line in out:
            parts = line.split("\t")
            if len(parts) < 6:
                continue
            cid, name, cpu_s, mem_s, net_s, blk_s = parts[:6]
            cpu_pct = float(cpu_s.strip().rstrip('%') or 0)
            mem_used_s = mem_s.split('/')[0].strip()
            net_in_s = net_s.split('/')[0].strip()
            net_out_s = net_s.split('/')[1].strip() if '/' in net_s else "0B"
            blk_in_s = blk_s.split('/')[0].strip()
            blk_out_s = blk_s.split('/')[1].strip() if '/' in blk_s else "0B"
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


def _host_apps(limit: int = 24) -> list[dict]:
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


def _runtime_status() -> dict:
    cfg = load_config()
    py = sys.executable
    web_rows = _find_exact_processes(["python3 -m systor serve web", f"{py} -m systor serve web"])
    collector_rows = _find_exact_processes(["python3 -m systor serve collector", f"{py} -m systor serve collector"])

    def _proc_row(rows: list[dict]) -> dict:
        if not rows:
            return {"running": False, "pid": None, "uptime_sec": None, "cpu_percent": None, "rss_mb": None, "vsz_mb": None}
        row = rows[0]
        return {"running": True, "pid": row["pid"], "uptime_sec": row["uptime_sec"]} | _pid_stats(row["pid"])

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
        "storage": _systor_storage_state(),
    }


def _build_channel_test_message(channel: str) -> str:
    snap = collect_snapshot()
    cpu = snap.get("cpu", {}) or {}
    mem = snap.get("memory", {}) or {}
    disks = snap.get("disks", []) or []
    worst_disk = max(disks, key=lambda d: d.get("used_pct", 0), default={})
    top_cpu = (read_top_processes(n=1, by="cpu") or [{}])[0]
    host = snap.get('hostname', 'systor')
    lines = [
        f"🖥️ {host}",
        f"🧠 CPU {cpu.get('percent', '?')}% · load {cpu.get('load_1m', '?')}",
        f"🧮 RAM free {mem.get('available_mb', '?')} MB · used {mem.get('used_mb', '?')} MB",
    ]
    if worst_disk:
        lines.append(f"💽 Disk {worst_disk.get('mount', '?')} {worst_disk.get('used_pct', '?')}%")
    if top_cpu.get('name'):
        lines.append(f"🔥 Top app {top_cpu.get('name')} · {top_cpu.get('cpu_percent', 0)}% CPU")
    prefix = "🧪 Systor Telegram test" if channel == 'telegram' else "🧪 Systor Discord test"
    return "\n".join([prefix, *lines])


def _looks_masked_secret(value: str) -> bool:
    value = (value or "").strip()
    return bool(value) and ("***" in value or "…" in value)


# ---------- Storage/File Manager safety helpers ----------
STORAGE_OP_LOG = Path("/var/lib/systor/storage_ops.jsonl")
_storage_deep_state = {"running": False, "done": False, "path": "", "started": 0, "ended": 0, "error": "", "result": None, "scanned": 0, "current": "", "folders_found": 0, "files_found": 0}
_storage_deep_lock = threading.Lock()
_DENY_PREFIXES = (
    "/etc", "/boot", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/proc", "/sys", "/dev", "/run",
    "/var/lib/docker", "/var/lib/containerd", "/root/.hermes", "/root/.ssh", "/root/.config", "/root/.cache",
)
_DENY_NAME_RE = re.compile(r"(\.env$|id_rsa|id_ed25519|authorized_keys|token|secret|credential|auth\.json)", re.I)


def _auto_storage_roots() -> list[str]:
    """Hardcoded user-visible roots. /mnt/tb and /root only.

    The previous findmnt-based auto-detect hung indefinitely on autofs/fuse
    mounts (e.g. /mnt/exhd1, rclone google-drive) when shutil.disk_usage()
    blocked on the FUSE driver. Keeping the list fixed and tiny avoids that
    class of bug entirely.
    """
    roots: list[str] = []
    for cand in ("/mnt/tb", "/root"):
        try:
            p = Path(cand)
            if p.exists() and p.is_dir() and str(p.resolve()) != "/":
                roots.append(str(p.resolve()))
        except Exception:
            pass
    return roots


def _storage_public_cfg(cfg: dict) -> dict:
    out = dict(cfg)
    out.pop("action_password_hash", None)
    out["has_action_password"] = bool(cfg.get("action_password_hash"))
    return out


def _verify_storage_action_password(body: dict | None = None) -> tuple[bool, str]:
    cfg = _storage_cfg()
    if not cfg.get("action_password_required"):
        return True, ""
    stored = cfg.get("action_password_hash") or ""
    if not stored:
        return False, "Set a Storage action password first in Settings"
    pw = ""
    if body:
        pw = str(body.get("action_password") or "")
    pw = pw or request.headers.get("X-Storage-Password", "") or request.args.get("pw", "")
    if not pw:
        return False, "Action password required"
    try:
        return (check_password_hash(stored, pw), "Invalid action password")
    except Exception:
        return False, "Invalid action password config"


def _storage_cfg() -> dict:
    cfg = load_config().setdefault("storage_page", {})
    roots = cfg.get("allowed_roots") or _auto_storage_roots()
    if isinstance(roots, str):
        allowed = [x.strip() for x in re.split(r"[\n,]+", roots) if x.strip()]
    elif isinstance(roots, list):
        allowed = [str(x).strip() for x in roots if str(x).strip()]
    else:
        allowed = _auto_storage_roots()
    if "/root" not in allowed and Path("/root").exists():
        allowed.append("/root")
    return {"allowed_roots": allowed or _auto_storage_roots(), "public_readonly": bool(cfg.get("public_readonly", True)), "trash_enabled": bool(cfg.get("trash_enabled", True)), "no_right_click": bool(cfg.get("no_right_click", False)), "action_password_required": bool(cfg.get("action_password_required", False)), "action_password_hash": str(cfg.get("action_password_hash") or ""), "max_scan_files": max(100, min(200000, int(cfg.get("max_scan_files", 20000) or 20000)))}

def _client_private() -> bool:
    if request.headers.get("CF-Connecting-IP") or request.headers.get("Cf-Ray"):
        return False
    raw = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    try:
        ip = ipaddress.ip_address(raw)
        return ip.is_loopback or ip.is_private or ip in ipaddress.ip_network("100.64.0.0/10")
    except Exception:
        return False


def _auth_cfg() -> dict:
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


def _auth_enabled() -> bool:
    cfg = _auth_cfg()
    return bool(cfg.get("enabled") and cfg.get("username") and cfg.get("password_hash"))


def _request_ip() -> str:
    raw = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    return raw or "unknown"


def _login_rate_key(scope: str, value: str) -> str:
    return f"{scope}:{value.strip().lower() if scope == 'user' else value.strip()}"


def _login_rate_status(ip: str, username: str = "") -> tuple[bool, int]:
    now = time.time()
    keys = [_login_rate_key("ip", ip)]
    if username:
        keys.append(_login_rate_key("user", username))
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


def _login_rate_fail(ip: str, username: str = "") -> tuple[bool, int]:
    now = time.time()
    auth = _auth_cfg()
    max_fails = int(auth.get("max_fails") or _LOGIN_MAX_FAILS)
    cooldown_sec = int(auth.get("cooldown_sec") or _LOGIN_COOLDOWN_SEC)
    keys = [_login_rate_key("ip", ip)]
    if username:
        keys.append(_login_rate_key("user", username))
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


def _login_rate_success(ip: str, username: str = "") -> None:
    keys = [_login_rate_key("ip", ip)]
    if username:
        keys.append(_login_rate_key("user", username))
    with _login_rate_lock:
        for key in keys:
            _login_rate_state.pop(key, None)


def _auth_session_expired() -> tuple[bool, int]:
    if not _auth_logged_in():
        return False, 0
    timeout_min = int(_auth_cfg().get("idle_timeout_min") or 0)
    if timeout_min <= 0:
        return False, 0
    now = int(time.time())
    last_seen = int(session.get("systor_last_seen") or 0)
    if last_seen and now - last_seen > timeout_min * 60:
        return True, timeout_min
    return False, timeout_min


def _auth_touch_session() -> None:
    if not _auth_logged_in():
        return
    session.permanent = True
    session["systor_last_seen"] = int(time.time())


def _auth_logout_session() -> None:
    session.pop("systor_auth", None)
    session.pop("systor_user", None)
    session.pop("systor_last_seen", None)


def _auth_logged_in() -> bool:
    cfg = _auth_cfg()
    return bool(session.get("systor_auth") and session.get("systor_user") == cfg.get("username"))


def _private_auth_exempt(path: str) -> bool:
    if path.startswith("/static/"):
        return True
    return path in ("/login", "/logout", "/health", "/api/version")


def _private_auth_protected(path: str) -> bool:
    if _private_auth_exempt(path):
        return False
    mode = _auth_cfg().get("mode", "admin_only")
    if mode == "full_app":
        return True
    admin_pages = ("/apps", "/network", "/speed", "/alerts", "/logs", "/settings", "/storage")
    if path in admin_pages or any(path.startswith(p + "/") for p in admin_pages):
        return True
    if path.startswith("/api/"):
        allow = {"/api/snapshot", "/api/series", "/api/network-series", "/api/public-dashboard", "/api/version"}
        return path not in allow
    return False


def _effective_private_ui() -> bool:
    private = _client_private()
    if not private:
        return False
    if not _auth_enabled():
        return True
    return _auth_logged_in()


def _storage_can_write() -> bool:
    return _client_private() and (not _auth_enabled() or _auth_logged_in())


def _safe_roots() -> list[Path]:
    out=[]
    for r in _storage_cfg()["allowed_roots"]:
        try:
            rp=Path(r).expanduser().resolve()
            if rp.exists() and str(rp) != "/": out.append(rp)
        except Exception:
            continue
    if not out:
        fb=Path("/mnt/tb")
        if fb.exists(): out.append(fb.resolve())
    return out


def _storage_roots_payload() -> list[dict]:
    rows = []
    for r in _safe_roots():
        label = "Data " + str(r) if str(r) == "/mnt/tb" else "/root" if str(r) == "/root" else (r.name or str(r))
        rows.append({"path": str(r), "label": label, "readonly": False})
    if Path("/").exists():
        rows.append({"path": "/", "label": "Root / SSD", "readonly": True})
    return rows


def _under(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root); return True
    except ValueError:
        return False


def _resolve_storage_path(value: str | None, must_exist: bool = True) -> Path:
    roots=_safe_roots()
    if not roots: abort(400, "no allowed storage roots configured")
    raw=(value or str(roots[0])).strip()
    p=Path(raw).expanduser()
    if not p.is_absolute(): p=roots[0] / p
    try: rp=p.resolve(strict=must_exist)
    except FileNotFoundError: rp=p.parent.resolve(strict=True) / p.name
    sp=str(rp)
    if sp == "/" or any(sp == x or sp.startswith(x + "/") for x in _DENY_PREFIXES): abort(403, "blocked system or secret path")
    if _DENY_NAME_RE.search(sp): abort(403, "blocked secret-like path")
    if not any(_under(root, rp) for root in roots): abort(403, "path escapes allowed roots")
    return rp


def _resolve_storage_browse_path(value: str | None) -> tuple[Path, bool]:
    """Resolve browse path. Returns (path, managed). Managed means actions may be allowed.
    LAN-only Storage can browse shallow safe system roots, but actions stay restricted to allowlisted roots.
    """
    raw=(value or "").strip()
    if raw == "/":
        return Path("/"), False
    p=Path(raw).expanduser()
    if not p.is_absolute():
        roots=_safe_roots(); p=(roots[0] / p) if roots else Path("/mnt/tb") / p
    rp=p.resolve(strict=True)
    sp=str(rp)
    if sp == "/" or any(sp == x or sp.startswith(x + "/") for x in _DENY_PREFIXES):
        abort(403, "blocked system or secret path")
    if _DENY_NAME_RE.search(sp):
        abort(403, "blocked secret-like path")
    managed=any(_under(root, rp) for root in _safe_roots())
    return rp, managed


def _fmt_ts(ts: float) -> str:
    try: return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception: return "—"


def _dir_size_limited(path: Path, max_files: int = 1200) -> tuple[int, int, int]:
    total=files=dirs=0; seen=0
    try:
        for root, dnames, fnames in os.walk(path):
            dnames[:] = [d for d in dnames if d not in (".systor-trash", ".Trash")]
            dirs += len(dnames)
            for name in fnames:
                seen += 1
                if seen > max_files: return total, files, dirs
                try:
                    fp=Path(root)/name
                    if fp.is_symlink(): continue
                    total += fp.stat().st_size; files += 1
                except Exception: continue
    except Exception: pass
    return total, files, dirs


def _entry_payload(p: Path) -> dict:
    try: st=p.lstat()
    except Exception: st=None
    typ = "symlink" if p.is_symlink() else "dir" if p.is_dir() else "file"
    size = 0
    if typ == "file" and st:
        size = st.st_size
    elif typ == "dir":
        # Fast immediate-children size for browser listings. Recursive size is
        # available on demand via the Deep Scan tool — using recursion here
        # made /api/storage/browse hang for ~2s on large folders.
        size, _f, _d = _dir_size_immediate(p)
    return {"name": p.name or str(p), "path": str(p), "type": typ, "size": size, "mtime": st.st_mtime if st else 0, "mtime_text": _fmt_ts(st.st_mtime if st else 0)}


def _dir_size_immediate(path: Path) -> tuple[int, int, int]:
    """Sum sizes of immediate children only. O(1) system calls per entry. Fast."""
    total = files = dirs = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_file(follow_symlinks=False):
                        try:
                            total += entry.stat(follow_symlinks=False).st_size
                            files += 1
                        except Exception:
                            pass
                    elif entry.is_dir(follow_symlinks=False):
                        dirs += 1
                except Exception:
                    continue
    except Exception:
        pass
    return total, files, dirs


def _mount_payload(path: Path) -> dict:
    """Disk usage for a mount, bounded so a hung FUSE driver can't lock the request."""
    try:
        usage = _disk_usage_bounded(path, timeout=2.0)
    except Exception:
        return {"mount": str(path), "total": 0, "used": 0, "free": 0, "used_pct": 0, "unavailable": True}
    if usage is None:
        return {"mount": str(path), "total": 0, "used": 0, "free": 0, "used_pct": 0, "unavailable": True}
    total, used, free = usage
    return {
        "mount": str(path),
        "total": total,
        "used": used,
        "free": free,
        "used_pct": round((used / total) * 100, 1) if total else 0,
    }


def _disk_usage_bounded(path: Path, timeout: float = 2.0):
    """shutil.disk_usage() can block forever on a hung FUSE mount.
    Run it in a thread with a hard timeout. Returns (total, used, free) or None on timeout/error.
    """
    import threading
    box: dict = {}
    def _run():
        try:
            u = shutil.disk_usage(str(path))
            box["ok"] = (u.total, u.used, u.free)
        except Exception as e:
            box["err"] = str(e)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None
    return box.get("ok")


def _log_storage_op(action: str, path: str, ok: bool, error: str = "") -> None:
    try:
        STORAGE_OP_LOG.parent.mkdir(parents=True, exist_ok=True)
        row={"ts": int(time.time()), "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "action": action, "path": path, "ok": bool(ok), "error": error[:300]}
        with STORAGE_OP_LOG.open("a") as f: f.write(json.dumps(row, ensure_ascii=False)+"\n")
    except Exception: pass


def _trash_path(p: Path) -> Path:
    root=next((r for r in _safe_roots() if _under(r, p)), _safe_roots()[0])
    trash=root/".systor-trash"/datetime.now().strftime("%Y%m%d-%H%M%S")
    trash.mkdir(parents=True, exist_ok=True)
    return trash / p.name


def _virtual_root_entries() -> list[dict]:
    rows=[]
    for p in Path("/").iterdir():
        try:
            sp=str(p.resolve())
            if any(sp == x or sp.startswith(x + "/") for x in _DENY_PREFIXES):
                continue
            st = p.lstat()
            typ = "symlink" if p.is_symlink() else "dir" if p.is_dir() else "file"
            rows.append({"name": p.name or str(p), "path": str(p), "type": typ, "size": st.st_size if typ == "file" else None, "mtime": st.st_mtime, "mtime_text": _fmt_ts(st.st_mtime)})
        except Exception:
            continue
    rows.sort(key=lambda r: (r.get("type") != "dir", r.get("name", "").lower()))
    return rows


def _quick_storage_analysis(path: Path, limit: int) -> dict:
    files=[]; folders=[]; old=[]; type_map={}; scanned=0; now=time.time()
    if str(path) == "/":
        entries = _virtual_root_entries()
    else:
        try:
            entries = [_entry_payload(x) for x in path.iterdir() if not x.is_symlink() and x.name not in (".systor-trash", ".Trash")]
        except Exception:
            entries = []
    for e in entries:
        scanned += 1
        if scanned > limit:
            break
        if e.get("type") == "dir":
            folders.append({"path": e["path"], "size": int(e.get("size") or 0)})
        else:
            size = int(e.get("size") or 0)
            mtime = float(e.get("mtime") or now)
            age = int((now-mtime)/86400)
            rec={"path": e["path"], "size": size, "mtime": mtime, "age_days": age}
            files.append(rec)
            if size > 50*1024*1024 and age >= 30: old.append(rec)
            ext=Path(e["path"]).suffix.lower() or "[none]"
            cur=type_map.setdefault(ext, {"ext": ext, "count": 0, "size": 0}); cur["count"] += 1; cur["size"] += size
    files.sort(key=lambda x:x["size"], reverse=True); folders.sort(key=lambda x:x["size"], reverse=True); old.sort(key=lambda x:(x["age_days"], x["size"]), reverse=True)
    dup_map={}
    for rec in files:
        key=(Path(rec["path"]).name.lower(), rec["size"]); dup_map.setdefault(key, []).append(rec)
    dups=[{"name": k[0], "size": k[1], "count": len(v), "paths": [g["path"] for g in v[:6]]} for k,v in dup_map.items() if len(v)>1 and k[1]>0]
    dups.sort(key=lambda x:(x["count"], x["size"]), reverse=True)
    types=sorted(type_map.values(), key=lambda x:x["size"], reverse=True)
    return {"files": files, "folders": folders, "old_files": old, "types": types, "duplicates": dups, "scanned": scanned, "summary": {"files": len(files), "folders": len(folders), "old_large": len(old), "duplicates": len(dups), "types": len(types), "total_file_size": sum(x.get("size",0) for x in files)}}


def _scan_storage(path: Path, limit: int) -> dict:
    files=[]; folders=[]; old=[]; type_map={}; scanned=0; now=time.time()
    for root, dnames, fnames in os.walk(path):
        dnames[:] = [d for d in dnames if d not in (".systor-trash", ".Trash")]
        rpath=Path(root)
        if rpath != path:
            sz, _, _ = _dir_size_limited(rpath, 800); folders.append({"path": str(rpath), "size": sz})
        for name in fnames:
            scanned += 1
            if scanned > limit: break
            fp=rpath/name
            try:
                if fp.is_symlink(): continue
                st=fp.stat(); size=st.st_size; ext=fp.suffix.lower() or "[none]"
                rec={"path": str(fp), "size": size, "mtime": st.st_mtime, "age_days": int((now-st.st_mtime)/86400)}
                files.append(rec)
                if scanned == 1 or scanned % 50 == 0:
                    with _storage_deep_lock:
                        if _storage_deep_state.get("running"):
                            _storage_deep_state.update({"scanned": scanned, "current": str(fp), "folders_found": len(folders), "files_found": len(files)})
                if size > 50*1024*1024 and rec["age_days"] >= 30: old.append(rec)
                cur=type_map.setdefault(ext, {"ext": ext, "count": 0, "size": 0}); cur["count"] += 1; cur["size"] += size
            except Exception: continue
        if scanned > limit: break
    files.sort(key=lambda x:x["size"], reverse=True); folders.sort(key=lambda x:x["size"], reverse=True); old.sort(key=lambda x:(x["age_days"], x["size"]), reverse=True)
    dup_map={}
    for rec in files:
        key=(Path(rec["path"]).name.lower(), rec["size"])
        dup_map.setdefault(key, []).append(rec)
    dups=[]
    for (_name, _size), group in dup_map.items():
        if len(group) > 1 and _size > 0:
            dups.append({"name": _name, "size": _size, "count": len(group), "paths": [g["path"] for g in group[:6]]})
    dups.sort(key=lambda x:(x["count"], x["size"]), reverse=True)
    types = sorted(type_map.values(), key=lambda x:x["size"], reverse=True)
    return {"files": files, "folders": folders, "old_files": old, "types": types, "duplicates": dups, "scanned": scanned, "summary": {"files": len(files), "folders": len(folders), "old_large": len(old), "duplicates": len(dups), "types": len(types), "total_file_size": sum(x.get("size",0) for x in files)}}


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    auth_boot = _auth_cfg()
    app.secret_key = auth_boot.get("session_secret") or os.environ.get("SYSTOR_SESSION_SECRET") or "systor-unsafe-dev-secret"
    app.config["JSON_SORT_KEYS"] = False
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 300
    app.static_folder = "static"

    @app.after_request
    def _static_cache_headers(response):
        try:
            path = request.path or ""
        except Exception:
            path = ""
        if path.startswith("/static/") or path.endswith((".css", ".js", ".png", ".svg", ".ico", ".webp", ".woff2")):
            response.headers["Cache-Control"] = "public, max-age=300"
        return response

    @app.context_processor
    def _ctx_access():
        private = _client_private()
        effective_private = _effective_private_ui()
        auth = _auth_cfg()
        return {
            "storage_private": effective_private,
            "client_private": effective_private,
            "client_private_raw": private,
            "auth_enabled": _auth_enabled(),
            "auth_mode": auth.get("mode", "admin_only"),
            "auth_username": auth.get("username", "admin"),
            "auth_idle_timeout_min": auth.get("idle_timeout_min", 0),
            "auth_max_fails": auth.get("max_fails", _LOGIN_MAX_FAILS),
            "auth_cooldown_sec": auth.get("cooldown_sec", _LOGIN_COOLDOWN_SEC),
            "auth_logged_in": _auth_logged_in(),
        }

    @app.before_request
    def _block_public_surfaces():
        path = request.path or ""
        private = _client_private()
        if private:
            if _auth_enabled() and _private_auth_protected(path):
                expired, timeout_min = _auth_session_expired()
                if expired:
                    _auth_logout_session()
                    if path.startswith("/api/"):
                        return jsonify({"ok": False, "error": f"session expired after {timeout_min}m idle"}), 401
                    next_url = request.full_path if request.query_string else request.path
                    if next_url.endswith("?"):
                        next_url = next_url[:-1]
                    return redirect(url_for("page_login", next=next_url))
                if not _auth_logged_in():
                    if path.startswith("/api/"):
                        return jsonify({"ok": False, "error": "login required"}), 401
                    next_url = request.full_path if request.query_string else request.path
                    if next_url.endswith("?"):
                        next_url = next_url[:-1]
                    return redirect(url_for("page_login", next=next_url))
                _auth_touch_session()
            return None

        # Public internet: dashboard only.
        public_pages_blocked = ("/apps", "/network", "/speed", "/alerts", "/logs", "/settings", "/storage", "/login", "/logout")
        if path in public_pages_blocked or any(path.startswith(p + "/") for p in public_pages_blocked):
            abort(404)

        # Public internet: allow only read-only dashboard data.
        public_api_allow = {
            "/api/snapshot",
            "/api/series",
            "/api/network-series",
            "/api/public-dashboard",
            "/api/version",
            "/health",
        }
        if path.startswith("/api/") and path not in public_api_allow:
            return jsonify({"ok": False, "error": "This API is LAN/Tailscale/local only"}), 403

        if path.startswith("/storage"):
            abort(404)

    # ---------- API: live snapshot ----------
    @app.route("/api/snapshot")
    def api_snapshot():
        s = collect_snapshot()
        s["db_stats"] = get_storage().stats()
        body = jsonify(s).get_data()
        import hashlib
        etag = '"' + hashlib.md5(body).hexdigest()[:16] + '"'
        if request.headers.get("If-None-Match") == etag:
            resp = Response(status=304)
            resp.headers["ETag"] = etag
            resp.headers["Cache-Control"] = "private, max-age=2"
            return resp
        resp = Response(body, mimetype="application/json")
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "private, max-age=2"
        return resp

    @app.route("/api/series")
    def api_series():
        metric = request.args.get("metric", "cpu_pct")
        hours = _parse_hours_arg(request.args.get("hours"), 6.0)
        data = get_storage().series(metric, hours=hours)
        data = _bucket_series_points(data, 600)
        return jsonify({"metric": metric, "hours": hours, "data": data})

    @app.route("/api/alerts")
    def api_alerts():
        limit = int(request.args.get("limit", 50))
        return jsonify(get_storage().recent_alerts(limit=limit))

    @app.route("/api/top-processes")
    def api_top_processes():
        """Top N processes by CPU% or memory MB.

        CPU% requires at least one previous scan to compute the delta. The
        first call always returns 0% for everything. Subsequent calls
        (every ~30s by default from the collector's poll interval) have
        real values.
        """
        try:
            n = max(1, min(50, int(request.args.get("n", 10))))
        except ValueError:
            n = 10
        by = request.args.get("by", "cpu")
        if by not in ("cpu", "mem"):
            by = "cpu"
        procs = read_top_processes(n=n, by=by)
        # Add mem_percent based on total memory
        total_mb = read_total_memory_mb()
        for p in procs:
            p["mem_percent"] = round(100.0 * p["mem_mb"] / total_mb, 1) if total_mb else 0.0
        return jsonify({"by": by, "n": len(procs), "processes": procs, "total_memory_mb": total_mb})

    @app.route("/api/notifications")
    def api_notifications():
        limit = int(request.args.get("limit", 50))
        return jsonify(get_storage().recent_notifications(limit=limit))

    @app.route("/api/access")
    def api_access():
        """Best-effort access surface summary for localhost/LAN/Tailscale/tunnel."""
        cfg = load_config()
        host = cfg.get("web", {}).get("host", "0.0.0.0")
        port = int(cfg.get("web", {}).get("port", 6677))
        lan_ips = []
        try:
            all_ipv4 = [x for x in subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=3).stdout.split() if "." in x]
            preferred = ""
            try:
                route_probe = subprocess.run(["bash", "-lc", "ip -4 route get 1.1.1.1 | sed -n 's/.* src \\([^ ]*\\).*/\\1/p'"], capture_output=True, text=True, timeout=3).stdout.strip()
                if route_probe and route_probe != "127.0.0.1":
                    preferred = route_probe
            except Exception:
                preferred = ""
            if preferred:
                lan_ips.append(preferred)
            for ip in all_ipv4:
                if ip.startswith("100.") or ip == "127.0.0.1" or ip in lan_ips:
                    continue
                if ip.startswith("192.168.") or ip.startswith("10."):
                    lan_ips.append(ip)
        except Exception:
            pass
        tailscale_ips = []
        try:
            if subprocess.run(["bash", "-lc", "command -v tailscale >/dev/null"], timeout=3).returncode == 0:
                tailscale_ips = [x for x in subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5).stdout.split() if x]
        except Exception:
            pass
        cloudflare = {"running": False, "process": ""}
        try:
            out = subprocess.run(["pgrep", "-af", "cloudflared"], capture_output=True, text=True, timeout=3).stdout.strip().splitlines()
            if out:
                cloudflare = {"running": True, "process": out[0][:240]}
        except Exception:
            pass
        return jsonify({
            "ok": True,
            "bind_host": host,
            "port": port,
            "localhost_urls": [f"http://127.0.0.1:{port}", f"http://localhost:{port}"],
            "lan_urls": [f"http://{ip}:{port}" for ip in lan_ips if not ip.startswith("100.") and ip != "127.0.0.1"],
            "tailscale_urls": [f"http://{ip}:{port}" for ip in tailscale_ips],
            "cloudflare": cloudflare,
            "note": "0.0.0.0 means one listener serves localhost, LAN, and Tailscale. Cloudflare Tunnel works if its route targets this port.",
        })

    @app.route("/api/storage/settings", methods=["GET", "POST"])
    def api_storage_settings():
        cfg = load_config()
        scfg = _storage_cfg()
        if request.method == "POST":
            if not _storage_can_write():
                return jsonify({"ok": False, "error": "storage settings are local/LAN/Tailscale only"}), 403
            body = request.get_json(silent=True) or {}
            roots = body.get("allowed_roots") or []
            if isinstance(roots, str):
                roots = [x.strip() for x in re.split(r"[\n,]+", roots) if x.strip()]
            clean=[]
            for r in roots:
                try:
                    rp=Path(str(r)).expanduser().resolve()
                    if rp.exists() and str(rp) != "/" and not any(str(rp)==x or str(rp).startswith(x+"/") for x in _DENY_PREFIXES):
                        clean.append(str(rp))
                except Exception:
                    continue
            cfg.setdefault("storage_page", {})
            if clean:
                cfg["storage_page"]["allowed_roots"] = ",".join(clean)
            cfg["storage_page"]["public_readonly"] = bool(body.get("public_readonly", True))
            cfg["storage_page"]["trash_enabled"] = bool(body.get("trash_enabled", True))
            cfg["storage_page"]["no_right_click"] = bool(body.get("no_right_click", False))
            cfg["storage_page"]["action_password_required"] = bool(body.get("action_password_required", False))
            new_pw = str(body.get("action_password") or "")
            if new_pw:
                cfg["storage_page"]["action_password_hash"] = generate_password_hash(new_pw)
            try: cfg["storage_page"]["max_scan_files"] = max(100, min(200000, int(body.get("max_scan_files", 20000))))
            except Exception: pass
            save_config(cfg)
            return jsonify({"ok": True, "message": "Storage settings saved", "config": _storage_public_cfg(_storage_cfg()), "roots": _storage_roots_payload(), "can_write": _storage_can_write()})
        return jsonify({"ok": True, "config": _storage_public_cfg(scfg), "roots": _storage_roots_payload(), "can_write": _storage_can_write(), "public": not _client_private()})

    @app.route("/api/storage/mounts")
    def api_storage_mounts():
        rows=[]
        seen=set()
        candidates=[("Root / SSD", "/")]
        for r in _auto_storage_roots():
            label = "Data " + r if r == "/mnt/tb" else Path(r).name or r
            candidates.append((label, r))
        for label, path in candidates:
            try:
                p=Path(path)
                if not p.exists():
                    continue
                payload=_mount_payload(p)
                key=(payload.get("total"), payload.get("mount"))
                if key in seen:
                    continue
                seen.add(key)
                payload.update({"label": label, "path": str(p.resolve())})
                rows.append(payload)
            except Exception:
                continue
        return jsonify({"ok": True, "mounts": rows})

    @app.route("/api/storage/browse")
    def api_storage_browse():
        raw_path = (request.args.get("path") or "").strip()
        if raw_path == "/":
            path = Path("/")
            managed = False
            entries = _virtual_root_entries()
        else:
            path, managed = _resolve_storage_browse_path(raw_path)
            if not path.is_dir():
                return jsonify({"ok": False, "error": "path is not a directory"}), 400
            entries=[]
            try:
                for child in sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                    try:
                        if _DENY_NAME_RE.search(str(child)): continue
                        # In unmanaged/root-ish folders, keep dir sizes unknown so root stays fast and not misleading.
                        if managed:
                            rec = _entry_payload(child)
                        else:
                            try:
                                st_u = child.lstat()
                                rec = {
                                    "name": child.name or str(child),
                                    "path": str(child),
                                    "type": "symlink" if child.is_symlink() else "dir" if child.is_dir() else "file",
                                    "size": st_u.st_size if child.is_file() else None,
                                    "mtime": st_u.st_mtime,
                                    "mtime_text": _fmt_ts(st_u.st_mtime),
                                }
                            except Exception:
                                rec = {"name": child.name or str(child), "path": str(child), "type": "file", "size": 0, "mtime": 0, "mtime_text": "—"}
                        entries.append(rec)
                    except Exception:
                        continue
            except PermissionError:
                return jsonify({"ok": False, "error": "permission denied"}), 403
        type_map={}; total=files=dirs=0
        for rec in entries:
            total += rec.get("size", 0) or 0
            if rec["type"] == "dir":
                dirs += 1
            else:
                files += 1
                ext = Path(rec.get("name") or rec.get("path") or "").suffix.lower() or "[none]"
                cur=type_map.setdefault(ext, {"ext": ext, "count": 0, "size": 0})
                cur["count"] += 1; cur["size"] += rec.get("size", 0) or 0
        return jsonify({"ok": True, "path": str(path), "entries": entries, "summary": {"total_size": total, "files": files, "dirs": dirs}, "types": sorted(type_map.values(), key=lambda x:x["size"], reverse=True)[:20], "mount": _mount_payload(path), "can_write": _storage_can_write() and managed and str(path) != "/"})

    @app.route("/api/storage/analysis")
    def api_storage_analysis():
        raw_path = (request.args.get("path") or "").strip()
        path = Path("/") if raw_path == "/" else _resolve_storage_browse_path(raw_path)[0]
        if not path.is_dir():
            return jsonify({"ok": False, "error": "path is not a directory"}), 400
        limit = max(1, min(200, int(request.args.get("limit", 25))))
        scfg = _storage_cfg()
        mode = request.args.get("mode", "quick")
        data = _scan_storage(path, min(scfg["max_scan_files"], 20000)) if mode == "deep" and str(path) != "/" else _quick_storage_analysis(path, min(scfg["max_scan_files"], 5000))
        return jsonify({"ok": True, "mode": mode if mode == "deep" else "quick", "path": str(path), "scanned": data["scanned"], "summary": data.get("summary", {}), "files": data["files"][:limit], "folders": data["folders"][:limit], "old_files": data["old_files"][:limit], "types": data["types"][:limit], "duplicates": data.get("duplicates", [])[:limit]})

    @app.route("/api/storage/preview")
    def api_storage_preview():
        p = _resolve_storage_path(request.args.get("path"), must_exist=True)
        if not p.is_file():
            return jsonify({"ok": False, "error": "preview supports files only"}), 400
        st = p.stat()
        suffix = p.suffix.lower()
        text_ext = {".txt", ".md", ".log", ".json", ".yaml", ".yml", ".toml", ".csv", ".py", ".sh", ".js", ".ts", ".css", ".html", ".xml"}
        image_ext = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
        if suffix in image_ext:
            return jsonify({"ok": True, "kind": "image", "name": p.name, "size": st.st_size, "download_url": "/api/storage/download?path=" + str(p)})
        if suffix in text_ext or st.st_size < 128*1024:
            try:
                data = p.read_text(errors="replace")[:12000]
                return jsonify({"ok": True, "kind": "text", "name": p.name, "size": st.st_size, "text": data})
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "kind": "binary", "name": p.name, "size": st.st_size, "message": "Binary/large file; use Download"})

    def _run_storage_deep_scan(path_str: str, limit: int):
        global _storage_deep_state
        with _storage_deep_lock:
            _storage_deep_state.update({"running": True, "done": False, "path": path_str, "started": time.time(), "ended": 0, "error": "", "result": None, "scanned": 0, "current": "", "folders_found": 0, "files_found": 0})
        try:
            path = _resolve_storage_browse_path(path_str)[0]
            data = _scan_storage(path, limit)
            with _storage_deep_lock:
                _storage_deep_state.update({"running": False, "done": True, "ended": time.time(), "result": data})
        except Exception as e:
            with _storage_deep_lock:
                _storage_deep_state.update({"running": False, "done": True, "ended": time.time(), "error": str(e), "result": None})

    @app.route("/api/storage/deep-scan", methods=["POST"])
    def api_storage_deep_scan_start():
        if not _client_private():
            return jsonify({"ok": False, "error": "deep scan is LAN/Tailscale/local only"}), 403
        body = request.get_json(silent=True) or {}
        ok, msg = _verify_storage_action_password(body)
        if not ok:
            return jsonify({"ok": False, "error": msg}), 403
        path = str(body.get("path") or "/mnt/tb")
        limit = max(100, min(_storage_cfg()["max_scan_files"], int(body.get("limit") or _storage_cfg()["max_scan_files"])))
        with _storage_deep_lock:
            if _storage_deep_state.get("running"):
                return jsonify({"ok": True, "message": "Deep scan already running", "state": _storage_deep_state})
        t = threading.Thread(target=_run_storage_deep_scan, args=(path, limit), daemon=True)
        t.start()
        return jsonify({"ok": True, "message": "Deep scan started", "path": path, "limit": limit})

    @app.route("/api/storage/deep-scan")
    def api_storage_deep_scan_status():
        with _storage_deep_lock:
            state = dict(_storage_deep_state)
        result = state.get("result")
        if result:
            state["result"] = {"scanned": result.get("scanned", 0), "summary": result.get("summary", {}), "files": result.get("files", [])[:50], "folders": result.get("folders", [])[:50], "old_files": result.get("old_files", [])[:50], "duplicates": result.get("duplicates", [])[:50]}
        return jsonify({"ok": True, "state": state})

    @app.route("/api/storage/checksum-duplicates")
    def api_storage_checksum_duplicates():
        if not _client_private():
            return jsonify({"ok": False, "error": "checksum scan is LAN/Tailscale/local only"}), 403
        ok, msg = _verify_storage_action_password()
        if not ok:
            return jsonify({"ok": False, "error": msg}), 403
        path = _resolve_storage_browse_path(request.args.get("path") or "/mnt/tb")[0]
        if not path.is_dir():
            return jsonify({"ok": False, "error": "path is not a directory"}), 400
        limit = max(100, min(8000, int(request.args.get("limit", 3000))))
        by_size = {}
        scanned = 0
        for root, dnames, fnames in os.walk(path):
            dnames[:] = [d for d in dnames if d not in (".systor-trash", ".Trash")]
            for name in fnames:
                if scanned >= limit:
                    break
                fp = Path(root) / name
                try:
                    if fp.is_symlink() or _DENY_NAME_RE.search(str(fp)):
                        continue
                    st = fp.stat()
                    if st.st_size <= 0:
                        continue
                    by_size.setdefault(st.st_size, []).append(str(fp))
                    scanned += 1
                except Exception:
                    continue
            if scanned >= limit:
                break
        groups = []
        for size, paths in by_size.items():
            if len(paths) < 2:
                continue
            hmap = {}
            for fp in paths:
                try:
                    h = hashlib.sha256()
                    with open(fp, "rb") as f:
                        for chunk in iter(lambda: f.read(1024*1024), b""):
                            h.update(chunk)
                    hmap.setdefault(h.hexdigest(), []).append(fp)
                except Exception:
                    continue
            for digest, g in hmap.items():
                if len(g) > 1:
                    groups.append({"sha256": digest, "size": size, "count": len(g), "paths": g[:10]})
        groups.sort(key=lambda x: (x["count"], x["size"]), reverse=True)
        return jsonify({"ok": True, "path": str(path), "scanned": scanned, "groups": groups[:50]})

    @app.route("/api/storage/upload-chunk", methods=["POST"])
    def api_storage_upload_chunk():
        if not _storage_can_write():
            return jsonify({"ok": False, "error": "upload is LAN/Tailscale/local only"}), 403
        ok, msg = _verify_storage_action_password()
        if not ok:
            return jsonify({"ok": False, "error": msg}), 403
        dest = _resolve_storage_path(request.args.get("path"), must_exist=True)
        if not dest.is_dir():
            return jsonify({"ok": False, "error": "destination is not a folder"}), 400
        filename = secure_filename(request.args.get("filename") or "")
        upload_id = re.sub(r"[^a-zA-Z0-9_.-]", "", request.args.get("upload_id") or "")[:80]
        if not filename or not upload_id:
            return jsonify({"ok": False, "error": "missing filename/upload_id"}), 400
        try:
            offset = int(request.args.get("offset") or 0)
            total = int(request.args.get("total") or 0)
        except Exception:
            return jsonify({"ok": False, "error": "bad offset/total"}), 400
        tmpdir = dest / ".systor-upload-tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        part = _resolve_storage_path(str(tmpdir / f"{upload_id}.part"), must_exist=False)
        data = request.get_data(cache=False)
        current = part.stat().st_size if part.exists() else 0
        if current != offset:
            return jsonify({"ok": False, "error": "offset mismatch", "expected_offset": current}), 409
        with part.open("ab") as f:
            f.write(data)
        written = offset + len(data)
        if total and written >= total:
            target = _resolve_storage_path(str(dest / filename), must_exist=False)
            if target.exists():
                stem, suffix = target.stem, target.suffix
                i = 1
                while target.exists() and i < 1000:
                    target = _resolve_storage_path(str(dest / f"{stem}-{i}{suffix}"), must_exist=False)
                    i += 1
            part.rename(target)
            _log_storage_op("upload-chunk", str(target), True)
            return jsonify({"ok": True, "done": True, "saved": str(target), "written": written})
        return jsonify({"ok": True, "done": False, "written": written})

    @app.route("/api/storage/upload", methods=["POST"])
    def api_storage_upload():
        if not _storage_can_write():
            return jsonify({"ok": False, "error": "upload is LAN/Tailscale/local only"}), 403
        ok, msg = _verify_storage_action_password()
        if not ok:
            return jsonify({"ok": False, "error": msg}), 403
        dest = _resolve_storage_path(request.args.get("path"), must_exist=True)
        if not dest.is_dir():
            return jsonify({"ok": False, "error": "destination is not a folder"}), 400
        files = request.files.getlist("files")
        if not files:
            return jsonify({"ok": False, "error": "no files uploaded"}), 400
        saved=[]
        try:
            for f in files:
                name = secure_filename(f.filename or "")
                if not name:
                    continue
                target = _resolve_storage_path(str(dest / name), must_exist=False)
                if target.exists():
                    stem, suffix = target.stem, target.suffix
                    i = 1
                    while target.exists() and i < 1000:
                        target = dest / f"{stem}-{i}{suffix}"
                        target = _resolve_storage_path(str(target), must_exist=False)
                        i += 1
                f.save(target)
                saved.append(str(target))
                _log_storage_op("upload", str(target), True)
            return jsonify({"ok": True, "message": f"Uploaded {len(saved)} file(s)", "saved": saved})
        except Exception as e:
            _log_storage_op("upload", str(dest), False, str(e))
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/storage/download")
    def api_storage_download():
        if not _client_private():
            abort(403, "download is LAN/Tailscale/local only")
        ok, msg = _verify_storage_action_password()
        if not ok:
            abort(403, msg)
        src = _resolve_storage_path(request.args.get("path"), must_exist=True)
        if src.is_file():
            return send_file(src, as_attachment=True, download_name=src.name)
        if src.is_dir():
            tmpdir = Path(tempfile.mkdtemp(prefix="systor-download-"))
            zpath = tmpdir / f"{src.name or 'download'}.zip"
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
                base = src.parent
                count = 0
                for root, dnames, fnames in os.walk(src):
                    dnames[:] = [d for d in dnames if d not in (".systor-trash", ".Trash")]
                    for name in fnames:
                        fp = Path(root) / name
                        if fp.is_symlink() or _DENY_NAME_RE.search(str(fp)):
                            continue
                        zf.write(fp, fp.relative_to(base))
                        count += 1
                        if count >= 5000:
                            break
                    if count >= 5000:
                        break
            @after_this_request
            def _cleanup(resp):
                try:
                    shutil.rmtree(tmpdir, ignore_errors=True)
                except Exception:
                    pass
                return resp
            return send_file(zpath, as_attachment=True, download_name=zpath.name)
        abort(400, "not a downloadable file or folder")

    @app.route("/api/storage/action", methods=["POST"])
    def api_storage_action():
        if not _storage_can_write():
            return jsonify({"ok": False, "error": "file actions are disabled on public/tunnel access; use LAN/Tailscale/local"}), 403
        body = request.get_json(silent=True) or {}
        ok, msg = _verify_storage_action_password(body)
        if not ok:
            return jsonify({"ok": False, "error": msg}), 403
        action = str(body.get("action") or "").strip().lower()
        base = _resolve_storage_path(body.get("path"), must_exist=True)
        items = body.get("items") or []
        if isinstance(items, str): items=[items]
        try:
            if action == "mkdir":
                name = re.sub(r"[\\/]+", "", str(body.get("name") or "").strip())
                if not name: raise ValueError("missing folder name")
                target = _resolve_storage_path(str(base / name), must_exist=False)
                target.mkdir(parents=False, exist_ok=False)
                _log_storage_op(action, str(target), True)
                return jsonify({"ok": True, "message": f"Created {target.name}"})
            if action in ("copy", "move"):
                dest = _resolve_storage_path(body.get("destination"), must_exist=True)
                if not dest.is_dir(): raise ValueError("destination is not a folder")
                for it in items:
                    src = _resolve_storage_path(it, must_exist=True)
                    target = dest / src.name
                    target = _resolve_storage_path(str(target), must_exist=False)
                    if action == "copy":
                        if src.is_dir(): shutil.copytree(src, target)
                        else: shutil.copy2(src, target)
                    else:
                        shutil.move(str(src), str(target))
                    _log_storage_op(action, f"{src} -> {target}", True)
                return jsonify({"ok": True, "message": f"{action} complete ({len(items)} item(s))"})
            if action == "rename":
                if len(items) != 1: raise ValueError("select exactly one item")
                src = _resolve_storage_path(items[0], must_exist=True)
                name = re.sub(r"[\\/]+", "", str(body.get("name") or "").strip())
                if not name: raise ValueError("missing new name")
                target = _resolve_storage_path(str(src.parent / name), must_exist=False)
                src.rename(target)
                _log_storage_op(action, f"{src} -> {target}", True)
                return jsonify({"ok": True, "message": "Renamed"})
            if action == "delete":
                if not _storage_cfg()["trash_enabled"]: raise ValueError("trash disabled; permanent delete not implemented")
                for it in items:
                    src = _resolve_storage_path(it, must_exist=True)
                    target = _trash_path(src)
                    shutil.move(str(src), str(target))
                    _log_storage_op("trash", f"{src} -> {target}", True)
                return jsonify({"ok": True, "message": f"Moved to trash ({len(items)} item(s))"})
            raise ValueError("unknown action")
        except Exception as e:
            _log_storage_op(action or "unknown", str(body.get("items") or body.get("path") or ""), False, str(e))
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/storage/ops")
    def api_storage_ops():
        rows=[]
        try:
            if STORAGE_OP_LOG.exists():
                rows=[json.loads(x) for x in STORAGE_OP_LOG.read_text().splitlines()[-100:] if x.strip()]
                rows=list(reversed(rows))
        except Exception:
            rows=[]
        return jsonify({"ok": True, "rows": rows})

    @app.route("/api/storage/trash")
    def api_storage_trash():
        rows=[]
        for root in _safe_roots():
            trash = root / ".systor-trash"
            if not trash.exists():
                continue
            try:
                for batch in sorted(trash.iterdir(), reverse=True):
                    if not batch.is_dir():
                        continue
                    for item in batch.iterdir():
                        try:
                            st=item.lstat()
                            rows.append({"name": item.name, "path": str(item), "root": str(root), "size": st.st_size if item.is_file() else None, "type": "dir" if item.is_dir() else "file", "mtime": st.st_mtime, "mtime_text": _fmt_ts(st.st_mtime)})
                        except Exception:
                            continue
            except Exception:
                continue
        return jsonify({"ok": True, "rows": rows[:300]})

    @app.route("/api/storage/restore", methods=["POST"])
    def api_storage_restore():
        if not _storage_can_write():
            return jsonify({"ok": False, "error": "restore is LAN/Tailscale/local only"}), 403
        body = request.get_json(silent=True) or {}
        ok, msg = _verify_storage_action_password(body)
        if not ok:
            return jsonify({"ok": False, "error": msg}), 403
        src = Path(str(body.get("trash_path") or "")).expanduser().resolve(strict=True)
        root = next((r for r in _safe_roots() if _under(r / ".systor-trash", src)), None)
        if not root:
            return jsonify({"ok": False, "error": "not a Systor trash item"}), 403
        dest_dir = _resolve_storage_path(body.get("destination") or str(root), must_exist=True)
        if not dest_dir.is_dir():
            return jsonify({"ok": False, "error": "destination is not a folder"}), 400
        target = dest_dir / src.name
        i=1
        while target.exists():
            target = dest_dir / f"{src.stem}-restored-{i}{src.suffix}"
            i += 1
        shutil.move(str(src), str(target))
        _log_storage_op("restore", f"{src} -> {target}", True)
        return jsonify({"ok": True, "message": "Restored", "target": str(target)})

    @app.route("/api/storage/ops/export")
    def api_storage_ops_export():
        ok, msg = _verify_storage_action_password()
        if not ok:
            abort(403, msg)
        if not STORAGE_OP_LOG.exists():
            STORAGE_OP_LOG.parent.mkdir(parents=True, exist_ok=True)
            STORAGE_OP_LOG.write_text("")
        return send_file(STORAGE_OP_LOG, as_attachment=True, download_name="systor-storage-ops.jsonl")

    @app.route("/api/storage/ops/clear", methods=["POST"])
    def api_storage_ops_clear():
        if not _storage_can_write():
            return jsonify({"ok": False, "error": "clear is LAN/Tailscale/local only"}), 403
        body = request.get_json(silent=True) or {}
        ok, msg = _verify_storage_action_password(body)
        if not ok:
            return jsonify({"ok": False, "error": msg}), 403
        STORAGE_OP_LOG.parent.mkdir(parents=True, exist_ok=True)
        STORAGE_OP_LOG.write_text("")
        return jsonify({"ok": True, "message": "Storage operation log cleared"})

    @app.route("/api/runtime")
    def api_runtime():
        return jsonify(_runtime_status())

    @app.route("/api/network-series")
    def api_network_series():
        hours = _parse_hours_arg(request.args.get("hours"), 24.0)
        data = get_storage().network_series(hours=hours)
        data = _bucket_network_points(data, 600)
        snap = collect_snapshot()
        return jsonify({"ok": True, "hours": hours, "data": data, "current": snap.get("network", {})})

    @app.route("/api/public-dashboard")
    def api_public_dashboard():
        """Single bundled read-only payload for the public dashboard.
        Cuts Cloudflare RTT overhead by collapsing many chart requests into one.
        Also uses a tiny in-process cache so burst refreshes don't rebuild all
        series repeatedly and cause random 3-5s spikes.
        """
        hours = _parse_hours_arg(request.args.get("hours"), 6.0)
        now = time.time()
        with _public_dash_cache_lock:
            cached_ts = float(_public_dash_cache.get("ts") or 0.0)
            cached_hours = _public_dash_cache.get("hours")
            cached_body = _public_dash_cache.get("body") or b""
            cached_etag = _public_dash_cache.get("etag") or ""
        if cached_body and cached_hours == hours and now - cached_ts < 2.0:
            if request.headers.get("If-None-Match") == cached_etag:
                resp = Response(status=304)
                resp.headers["ETag"] = cached_etag
                resp.headers["Cache-Control"] = "public, max-age=2"
                return resp
            resp = Response(cached_body, mimetype="application/json")
            resp.headers["ETag"] = cached_etag
            resp.headers["Cache-Control"] = "public, max-age=2"
            return resp
        snap = collect_snapshot()
        snap["db_stats"] = get_storage().stats()
        metrics = {
            "cpu": _bucket_series_points(get_storage().series("cpu_pct", hours=hours), 600),
            "temp": _bucket_series_points(get_storage().series("cpu_temp", hours=hours), 600),
            "mem": _bucket_series_points(get_storage().series("mem_used_mb", hours=hours), 600),
            "disk_read": _bucket_series_points(get_storage().series("disk_read_mbps", hours=hours), 600),
            "disk_write": _bucket_series_points(get_storage().series("disk_write_mbps", hours=hours), 600),
            "load1": _bucket_series_points(get_storage().series("load_1m", hours=hours), 600),
            "load5": _bucket_series_points(get_storage().series("load_5m", hours=hours), 600),
            "load15": _bucket_series_points(get_storage().series("load_15m", hours=hours), 600),
        }
        net = _bucket_network_points(get_storage().network_series(hours=hours), 600)
        body = json.dumps({"ok": True, "hours": hours, "snapshot": snap, "metrics": metrics, "network": net}, separators=(",", ":")).encode()
        etag = '"' + hashlib.md5(body).hexdigest()[:16] + '"'
        with _public_dash_cache_lock:
            _public_dash_cache["ts"] = now
            _public_dash_cache["hours"] = hours
            _public_dash_cache["body"] = body
            _public_dash_cache["etag"] = etag
        if request.headers.get("If-None-Match") == etag:
            resp = Response(status=304)
            resp.headers["ETag"] = etag
            resp.headers["Cache-Control"] = "public, max-age=2"
            return resp
        resp = Response(body, mimetype="application/json")
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "public, max-age=2"
        return resp

    @app.route("/api/network-usage")
    def api_network_usage():
        granularity = request.args.get("granularity", "day")
        limit = max(1, min(5000, int(request.args.get("limit", 30))))
        iface = (request.args.get("iface") or "all").strip()
        data = get_storage().network_usage_buckets(granularity=granularity, limit=limit, iface=iface)
        return jsonify({"ok": True, "granularity": granularity, "limit": limit, "iface": iface, "data": data, "stats": get_storage().stats()})

    @app.route("/api/network-interfaces")
    def api_network_interfaces():
        rows = read_network_interfaces()
        top_rx = max(rows, key=lambda r: r.get('rx_mbps', 0), default=None)
        top_tx = max(rows, key=lambda r: r.get('tx_mbps', 0), default=None)
        top_total = max(rows, key=lambda r: r.get('total_bytes', 0), default=None)
        return jsonify({"ok": True, "interfaces": rows, "top_rx": top_rx, "top_tx": top_tx, "top_total": top_total})

    @app.route("/api/speedtests")
    def api_speedtests():
        cfg = load_config()
        default_limit = int((cfg.get("speed", {}) or {}).get("history_page_size", 25))
        limit = max(1, min(500, int(request.args.get("limit", default_limit))))
        offset = max(0, int(request.args.get("offset", 0)))
        provider = (request.args.get("provider") or "all").strip().lower()
        run_type = (request.args.get("run_type") or "all").strip().lower()
        rows, total = get_storage().recent_speedtests(limit=limit, offset=offset, provider=provider, run_type=run_type)
        return jsonify({"ok": True, "rows": rows, "provider": provider, "run_type": run_type, "limit": limit, "offset": offset, "total": total})

    @app.route("/api/speed/status")
    def api_speed_status():
        cfg = load_config()
        rows, _total = get_storage().recent_speedtests(limit=200, provider="all")
        latest = []
        seen = set()
        for row in rows:
            prov = row.get("provider")
            if prov in seen:
                continue
            seen.add(prov)
            latest.append(row)
        port = int((cfg.get("speed", {}) or {}).get("iperf_port", 5201))
        return jsonify({"ok": True, "latest": latest, "iperf": iperf_status(port), "local_ips": local_ipv4s(), "config": cfg, "live": _speed_live_copy()})

    @app.route("/api/speed/live/start", methods=["POST"])
    def api_speed_live_start():
        body = request.get_json(silent=True) or {}
        server_id = str(body.get("server_id") or "").strip()
        target = str(body.get("target") or "").strip()
        run_type = str(body.get("run_type") or "manual").strip().lower()
        snap = _speed_live_copy()
        if snap.get("running"):
            return jsonify({"ok": False, "error": "speedtest already running", "live": snap}), 409
        _speed_live_reset(server_id=server_id, target=target, run_type=run_type)
        th = threading.Thread(target=_speed_live_worker, args=(server_id, run_type), daemon=True)
        th.start()
        time.sleep(0.2)
        return jsonify({"ok": True, "live": _speed_live_copy()})

    @app.route("/api/speed/live/status")
    def api_speed_live_status():
        return jsonify({"ok": True, "live": _speed_live_copy()})

    @app.route("/api/speed/live/stop", methods=["POST"])
    def api_speed_live_stop():
        global _speed_live_proc
        with _speed_live_lock:
            proc = _speed_live_proc
            _speed_live_state["cancel_requested"] = True
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception as e:
                return jsonify({"ok": False, "error": str(e), "live": _speed_live_copy()}), 500
        return jsonify({"ok": True, "live": _speed_live_copy()})

    @app.route("/api/speed/options")
    def api_speed_options():
        return jsonify({
            "ok": True,
            "ookla_servers": list_ookla_servers(limit=20),
            "librespeed_servers": list_librespeed_servers(limit=40),
        })

    @app.route("/api/speed/run", methods=["POST"])
    def api_speed_run():
        cfg = load_config()
        body = request.get_json(silent=True) or {}
        provider = str(body.get("provider") or "ookla").strip().lower()
        server_id = body.get("server_id")
        run_type = str(body.get("run_type") or "manual").strip().lower()
        providers = ["ookla", "librespeed", "notion", "cloudflare"] if provider == "all" else [provider]
        rows = run_many(providers, cfg=cfg, server_id=server_id, run_type=run_type)
        for row in rows:
            get_storage().log_speedtest(row)
        return jsonify({"ok": True, "rows": rows})

    @app.route("/api/speed/log", methods=["POST"])
    def api_speed_log():
        body = request.get_json(silent=True) or {}
        row = {
            "ts": int(body.get("ts") or time.time()),
            "provider": str(body.get("provider") or "cloudflare").strip().lower(),
            "target": str(body.get("target") or "browser test"),
            "mode": str(body.get("mode") or "browser"),
            "server_id": str(body.get("server_id") or ""),
            "run_type": str(body.get("run_type") or "manual"),
            "ping_ms": body.get("ping_ms"),
            "jitter_ms": body.get("jitter_ms"),
            "packet_loss": body.get("packet_loss"),
            "dl_mbps": body.get("dl_mbps"),
            "ul_mbps": body.get("ul_mbps"),
            "note": str(body.get("note") or "browser speed test"),
            "ok": bool(body.get("ok", True)),
            "raw_json": json.dumps(body)[:16000],
        }
        get_storage().log_speedtest(row)
        return jsonify({"ok": True, "row": row})

    @app.route("/api/speed/iperf/start", methods=["POST"])
    def api_speed_iperf_start():
        cfg = load_config()
        port = int((cfg.get("speed", {}) or {}).get("iperf_port", 5201))
        st = start_iperf_server(port)
        return jsonify({"ok": True, "status": st})

    @app.route("/api/apps")
    def api_apps():
        scope = request.args.get("scope", "all")
        sort_by = request.args.get("sort", "cpu")
        limit = max(1, min(1000, int(request.args.get("limit", 24))))

        now = time.time()
        fetch_limit = max(limit, 96)
        with _apps_cache_lock:
            cached_ts = float(_apps_cache.get("ts") or 0)
            cached_limit = int(_apps_cache.get("limit") or 0)
            cached_host = list(_apps_cache.get("host_rows") or [])
            cached_docker = list(_apps_cache.get("docker_rows") or [])
        # Cache TTL raised from 2s -> 6s so navigation back to the apps page
        # within a few seconds does not re-trigger the ~2s psutil/docker cold start.
        if cached_ts > 0 and now - cached_ts < 6.0 and cached_limit >= limit and (cached_host or cached_docker):
            host_rows = cached_host[:limit]
            docker_rows = cached_docker[:limit]
        else:
            host_all = _host_apps(limit=fetch_limit)
            docker_all = _docker_apps(limit=fetch_limit)
            with _apps_cache_lock:
                _apps_cache["ts"] = now
                _apps_cache["limit"] = fetch_limit
                _apps_cache["host_rows"] = host_all
                _apps_cache["docker_rows"] = docker_all
            host_rows = host_all[:limit]
            docker_rows = docker_all[:limit]
        rows = []
        if scope in ("all", "host"):
            rows.extend(host_rows)
        if scope in ("all", "docker"):
            rows.extend(docker_rows)
        if sort_by != "raw":
            sort_key = {
                "cpu": lambda r: (r.get("cpu_percent", 0), r.get("mem_mb", 0)),
                "mem": lambda r: (r.get("mem_mb", 0), r.get("cpu_percent", 0)),
                "net": lambda r: ((r.get("net_in_mb", 0) + r.get("net_out_mb", 0)), r.get("cpu_percent", 0)),
                "disk": lambda r: ((r.get("disk_in_mb", 0) + r.get("disk_out_mb", 0)), r.get("cpu_percent", 0)),
            }.get(sort_by, lambda r: (r.get("cpu_percent", 0), r.get("mem_mb", 0)))
            rows.sort(key=sort_key, reverse=True)
        rows = rows[:limit]
        return jsonify({
            "ok": True,
            "scope": scope,
            "sort": sort_by,
            "rows": rows,
            "host_count": len(host_rows),
            "docker_count": len(docker_rows),
            "summary": {
                "host_cpu": round(sum(r.get("cpu_percent", 0) for r in host_rows), 2),
                "host_mem_mb": round(sum(r.get("mem_mb", 0) for r in host_rows), 2),
                "host_disk_mb": round(sum((r.get("disk_in_mb", 0) + r.get("disk_out_mb", 0)) for r in host_rows), 2),
                "docker_cpu": round(sum(r.get("cpu_percent", 0) for r in docker_rows), 2),
                "docker_mem_mb": round(sum(r.get("mem_mb", 0) for r in docker_rows), 2),
                "docker_disk_mb": round(sum((r.get("disk_in_mb", 0) + r.get("disk_out_mb", 0)) for r in docker_rows), 2),
                "host_count": len(host_rows),
                "docker_count": len(docker_rows),
            }
        })

    @app.route("/api/system")
    def api_system():
        cfg = load_config()
        return jsonify({
            "config": cfg,
            "snapshot": collect_snapshot(),
            "db_stats": get_storage().stats(),
        })

    @app.route("/api/version")
    def api_version():
        from . import __version__, __app_name__
        return jsonify({"name": __app_name__, "version": __version__})

    # ---------- HTML pages ----------
    @app.route("/login", methods=["GET", "POST"])
    def page_login():
        if not _client_private():
            abort(404)
        if not _auth_enabled():
            return redirect(url_for("page_dashboard"))
        if _auth_logged_in():
            nxt = str(request.values.get("next") or "/")
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = "/"
            return redirect(nxt)
        error = ""
        next_url = str(request.values.get("next") or "/")
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/"
        ip = _request_ip()
        blocked, wait_sec = _login_rate_status(ip)
        if blocked:
            error = f"Too many failed logins. Try again in {wait_sec}s."
            status = 429 if request.method == "POST" else 200
            return render_template("login.html", error=error, next_url=next_url, wait_sec=wait_sec), status
        if request.method == "POST":
            auth = _auth_cfg()
            username = str(request.form.get("username") or "").strip()
            blocked, wait_sec = _login_rate_status(ip, username)
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
                    _login_rate_success(ip, username)
                    session["systor_auth"] = True
                    session["systor_user"] = auth.get("username")
                    session.permanent = True
                    session["systor_last_seen"] = int(time.time())
                    return redirect(next_url or "/")
            blocked, wait_sec = _login_rate_fail(ip, username)
            if blocked:
                error = f"Too many failed logins. Try again in {wait_sec}s."
                return render_template("login.html", error=error, next_url=next_url, wait_sec=wait_sec), 429
            error = "Invalid username or password"
            return render_template("login.html", error=error, next_url=next_url, wait_sec=0), 401
        return render_template("login.html", error=error, next_url=next_url, wait_sec=0)

    @app.route("/logout", methods=["GET", "POST"])
    def page_logout():
        if not _client_private():
            abort(404)
        _auth_logout_session()
        if _auth_enabled():
            return redirect(url_for("page_login"))
        return redirect(url_for("page_dashboard"))

    @app.route("/")
    def page_dashboard():
        cfg = load_config()
        return render_template("dashboard.html", cfg=cfg)

    @app.route("/alerts")
    def page_alerts():
        return render_template("alerts.html")

    @app.route("/logs")
    def page_logs():
        cfg = load_config()
        return render_template("logs.html", log_path=cfg.get("logging", {}).get("file", "/var/log/systor/systor.log"))

    @app.route("/network")
    def page_network():
        cfg = load_config()
        return render_template("network.html", cfg=cfg)

    @app.route("/apps")
    def page_apps():
        cfg = load_config()
        return render_template("apps.html", cfg=cfg)

    @app.route("/speed")
    def page_speed():
        cfg = load_config()
        return render_template("speed.html", cfg=cfg)

    @app.route("/storage")
    def page_storage():
        cfg = load_config()
        return render_template("storage.html", cfg=cfg)

    @app.route("/settings", methods=["GET", "POST"])
    def page_settings():
        if request.method == "POST":
            cfg = load_config()
            data = request.get_json(silent=True) or request.form.to_dict()
            def _bool(v):
                return str(v).lower() in ("true", "on", "1", "yes")
            # Thresholds — each metric now has {enabled, value, duration_min}
            threshold_specs = [
                # (key, cast, has_duration)
                ("cpu_load_1m",   float, True),
                ("cpu_temp_c",    float, True),
                ("mem_free_mb",   int,   True),
                ("swap_used_mb",  int,   True),
                ("disk_used_pct", int,   True),
            ]
            for key, cast, has_dur in threshold_specs:
                enabled_v = data.get(f"threshold_{key}_enabled")
                value_v   = data.get(f"threshold_{key}_value")
                dur_v     = data.get(f"threshold_{key}_duration")
                entry = cfg["thresholds"].get(key, {})
                if not isinstance(entry, dict):
                    entry = {"enabled": True, "value": cast(entry) if entry is not None else 0,
                             "duration_min": 2}
                if enabled_v is not None:
                    entry["enabled"] = _bool(enabled_v)
                if value_v is not None and value_v != "":
                    try: entry["value"] = cast(value_v)
                    except (ValueError, TypeError): pass
                if has_dur and dur_v is not None and dur_v != "":
                    try: entry["duration_min"] = max(1, int(float(dur_v)))
                    except (ValueError, TypeError): pass
                cfg["thresholds"][key] = entry
            # Cooldown
            cd = data.get("cooldown_sec")
            if cd is not None and cd != "":
                try: cfg["thresholds"]["cooldown_sec"] = max(0, int(float(cd)))
                except (ValueError, TypeError): pass
            # Update telegram
            if "telegram_enabled" in data:
                cfg["telegram"]["enabled"] = _bool(data.get("telegram_enabled"))
            if "telegram_bot_token" in data and data["telegram_bot_token"]:
                tok = str(data["telegram_bot_token"]).strip()
                if not _looks_masked_secret(tok):
                    cfg["telegram"]["bot_token"] = tok
            if "telegram_chat_id" in data:
                cfg["telegram"]["chat_id"] = str(data["telegram_chat_id"] or "").strip()
            # Update discord
            if "discord_enabled" in data:
                cfg["discord"]["enabled"] = _bool(data.get("discord_enabled"))
            if "discord_webhook_url" in data and data["discord_webhook_url"]:
                cfg["discord"]["webhook_url"] = str(data["discord_webhook_url"]).strip()
            # Update poll interval
            if "poll_interval_sec" in data and data["poll_interval_sec"]:
                try: cfg["collector"]["poll_interval_sec"] = max(1, int(data["poll_interval_sec"]))
                except (ValueError, TypeError): pass
            cfg.setdefault("network", {})
            if "network_default_hours" in data and data["network_default_hours"]:
                try: cfg["network"]["default_hours"] = max(0.25, min(24 * 3650, float(data["network_default_hours"])))
                except (ValueError, TypeError): pass
            if "network_auto_refresh_sec" in data and data["network_auto_refresh_sec"]:
                try: cfg["network"]["auto_refresh_sec"] = max(1, int(data["network_auto_refresh_sec"]))
                except (ValueError, TypeError): pass
            if "network_default_granularity" in data and data["network_default_granularity"] in ("day", "week", "month"):
                cfg["network"]["default_granularity"] = data["network_default_granularity"]
            if "network_default_bar_days" in data and data["network_default_bar_days"]:
                val = str(data["network_default_bar_days"]).strip()
                if val == "all": cfg["network"]["default_bar_days"] = "all"
                else:
                    try: cfg["network"]["default_bar_days"] = max(1, int(val))
                    except (ValueError, TypeError): pass
            if "network_default_table_days" in data and data["network_default_table_days"]:
                val = str(data["network_default_table_days"]).strip()
                if val == "all": cfg["network"]["default_table_days"] = "all"
                else:
                    try: cfg["network"]["default_table_days"] = max(1, int(val))
                    except (ValueError, TypeError): pass
            if "network_default_iface" in data:
                cfg["network"]["default_iface"] = str(data.get("network_default_iface") or "all").strip() or "all"
            if "network_hide_virtual_default" in data:
                cfg["network"]["hide_virtual_default"] = _bool(data.get("network_hide_virtual_default"))
            cfg.setdefault("dashboard", {})
            if "dashboard_default_hours" in data and data["dashboard_default_hours"]:
                try: cfg["dashboard"]["default_hours"] = max(0.25, min(24 * 3650, float(data["dashboard_default_hours"])))
                except (ValueError, TypeError): pass
            if "dashboard_refresh_sec" in data and data["dashboard_refresh_sec"]:
                try: cfg["dashboard"]["refresh_sec"] = max(1, int(data["dashboard_refresh_sec"]))
                except (ValueError, TypeError): pass
            if "dashboard_chart_refresh_sec" in data and data["dashboard_chart_refresh_sec"]:
                try: cfg["dashboard"]["chart_refresh_sec"] = max(1, int(data["dashboard_chart_refresh_sec"]))
                except (ValueError, TypeError): pass
            cfg.setdefault("apps", {})
            if "apps_auto_refresh_sec" in data and data["apps_auto_refresh_sec"]:
                try: cfg["apps"]["auto_refresh_sec"] = max(1, int(data["apps_auto_refresh_sec"]))
                except (ValueError, TypeError): pass
            if "apps_default_limit" in data and data["apps_default_limit"]:
                try: cfg["apps"]["default_limit"] = max(4, min(30, int(data["apps_default_limit"])))
                except (ValueError, TypeError): pass
            if "apps_default_scope" in data and data["apps_default_scope"] in ("all", "host", "docker"):
                cfg["apps"]["default_scope"] = data["apps_default_scope"]
            if "apps_default_sort" in data and data["apps_default_sort"] in ("cpu", "mem", "net", "disk"):
                cfg["apps"]["default_sort"] = data["apps_default_sort"]
            cfg.setdefault("speed", {})
            if "speed_page_refresh_sec" in data and data["speed_page_refresh_sec"]:
                try: cfg["speed"]["page_refresh_sec"] = max(0, int(data["speed_page_refresh_sec"]))
                except (ValueError, TypeError): pass
            if "speed_default_provider" in data and data["speed_default_provider"] in ("ookla", "librespeed", "notion", "cloudflare"):
                cfg["speed"]["default_provider"] = data["speed_default_provider"]
            if "speed_auto_enabled" in data:
                cfg["speed"]["auto_enabled"] = _bool(data.get("speed_auto_enabled"))
            if "speed_auto_provider" in data and data["speed_auto_provider"] in ("ookla", "librespeed", "notion", "cloudflare"):
                cfg["speed"]["auto_provider"] = data["speed_auto_provider"]
            if "speed_auto_interval_min" in data and data["speed_auto_interval_min"]:
                try: cfg["speed"]["auto_interval_min"] = max(5, int(float(data["speed_auto_interval_min"])))
                except (ValueError, TypeError): pass
            if "speed_notify_enabled" in data:
                cfg["speed"]["notify_enabled"] = _bool(data.get("speed_notify_enabled"))
            if "speed_min_download_mbps" in data and data["speed_min_download_mbps"] != "":
                try: cfg["speed"]["min_download_mbps"] = max(0.0, float(data["speed_min_download_mbps"]))
                except (ValueError, TypeError): pass
            if "speed_min_upload_mbps" in data and data["speed_min_upload_mbps"] != "":
                try: cfg["speed"]["min_upload_mbps"] = max(0.0, float(data["speed_min_upload_mbps"]))
                except (ValueError, TypeError): pass
            if "speed_librespeed_server_id" in data:
                cfg["speed"]["librespeed_server_id"] = str(data.get("speed_librespeed_server_id") or "").strip()
            if "speed_ookla_server_id" in data:
                cfg["speed"]["ookla_server_id"] = str(data.get("speed_ookla_server_id") or "").strip()
            if "speed_local_librespeed_port" in data and data["speed_local_librespeed_port"]:
                try: cfg["speed"]["local_librespeed_port"] = max(1, min(65535, int(data["speed_local_librespeed_port"])))
                except (ValueError, TypeError): pass
            if "speed_history_page_size" in data and data["speed_history_page_size"]:
                try: cfg["speed"]["history_page_size"] = max(5, min(100, int(data["speed_history_page_size"])))
                except (ValueError, TypeError): pass
            if "speed_iperf_port" in data and data["speed_iperf_port"]:
                try: cfg["speed"]["iperf_port"] = max(1, min(65535, int(data["speed_iperf_port"])))
                except (ValueError, TypeError): pass
            # Update web (host/port)
            if "web_host" in data and data["web_host"]:
                cfg["web"]["host"] = data["web_host"]
            if "web_port" in data and data["web_port"]:
                try: cfg["web"]["port"] = int(data["web_port"])
                except (ValueError, TypeError): pass
            # Optional single-admin auth
            cfg.setdefault("auth", {})
            auth = cfg["auth"]
            auth["enabled"] = _bool(data.get("auth_enabled")) if "auth_enabled" in data else bool(auth.get("enabled", False))
            if "auth_mode" in data and str(data.get("auth_mode") or "").strip() in ("admin_only", "full_app"):
                auth["mode"] = str(data.get("auth_mode") or "admin_only").strip()
            if "auth_username" in data:
                auth["username"] = str(data.get("auth_username") or "admin").strip() or "admin"
            if "auth_idle_timeout_min" in data and str(data.get("auth_idle_timeout_min") or "") != "":
                try: auth["idle_timeout_min"] = max(0, min(1440, int(float(data["auth_idle_timeout_min"]))))
                except (ValueError, TypeError): pass
            if "auth_max_fails" in data and str(data.get("auth_max_fails") or "") != "":
                try: auth["max_fails"] = max(2, min(20, int(float(data["auth_max_fails"]))))
                except (ValueError, TypeError): pass
            if "auth_cooldown_sec" in data and str(data.get("auth_cooldown_sec") or "") != "":
                try: auth["cooldown_sec"] = max(10, min(3600, int(float(data["auth_cooldown_sec"]))))
                except (ValueError, TypeError): pass
            clear_auth_password = _bool(data.get("auth_clear_password")) if "auth_clear_password" in data else False
            pw = str(data.get("auth_password") or "")
            pw2 = str(data.get("auth_password_confirm") or "")
            if clear_auth_password:
                auth["password_hash"] = ""
            if pw or pw2:
                if pw != pw2:
                    return jsonify({"ok": False, "message": "Auth password confirmation does not match."}), 400
                auth["password_hash"] = generate_password_hash(pw)
            if auth.get("enabled"):
                if not auth.get("username"):
                    return jsonify({"ok": False, "message": "Auth username is required when login protection is enabled."}), 400
                if not auth.get("password_hash"):
                    return jsonify({"ok": False, "message": "Set an auth password before enabling login protection."}), 400
                if not auth.get("session_secret"):
                    auth["session_secret"] = secrets.token_hex(32)
            if auth.get("session_secret"):
                app.secret_key = auth.get("session_secret") or app.secret_key
            try:
                path = save_config(cfg)
                msg = f"Saved to {path}."
            except PermissionError as e:
                msg = f"Could not save: {e}. Run install.sh or chmod the config file."
                return jsonify({"ok": False, "message": msg}), 500
            return jsonify({"ok": True, "message": msg})
        # GET
        cfg = load_config()
        return render_template("settings.html", cfg=cfg)

    @app.route("/api/test-telegram", methods=["POST"])
    def api_test_telegram():
        cfg = load_config()
        tg = cfg.get("telegram", {})
        body = request.get_json(silent=True) or {}
        # Allow Settings page to test currently typed values before saving.
        token = str(body.get("bot_token") or body.get("telegram_bot_token") or tg.get("bot_token", "")).strip()
        chat = str(body.get("chat_id") or body.get("telegram_chat_id") or tg.get("chat_id", "")).strip()
        if not token or not chat:
            return jsonify({"ok": False, "error": "bot_token or chat_id not set"}), 400
        if _looks_masked_secret(token):
            err = "saved Telegram token is masked/placeholder, not a real bot token — paste the full token and save again"
            get_storage().log_notification("telegram", False, err)
            return jsonify({"ok": False, "error": err}), 400
        msg = body.get("message") or _build_channel_test_message("telegram")
        ok, err = send_telegram(token, chat, msg)
        get_storage().log_notification("telegram", ok, err)
        return jsonify({"ok": ok, "error": err, "message": msg})

    @app.route("/api/test-discord", methods=["POST"])
    def api_test_discord():
        cfg = load_config()
        dc = cfg.get("discord", {})
        body = request.get_json(silent=True) or {}
        # Allow Settings page to test currently typed values before saving.
        url = str(body.get("webhook_url") or body.get("discord_webhook_url") or dc.get("webhook_url", "")).strip()
        if not url:
            return jsonify({"ok": False, "error": "webhook_url not set"}), 400
        msg = body.get("message") or _build_channel_test_message("discord")
        ok, err = send_discord(url, msg)
        get_storage().log_notification("discord", ok, err)
        return jsonify({"ok": ok, "error": err, "message": msg})

    @app.route("/api/config", methods=["GET"])
    def api_config_get():
        """Return current config (with secrets masked)."""
        cfg = load_config()
        cfg = json.loads(json.dumps(cfg))  # deep copy
        # Mask secrets
        if cfg.get("telegram", {}).get("bot_token"):
            tok = cfg["telegram"]["bot_token"]
            cfg["telegram"]["bot_token"] = tok[:6] + "…" + tok[-4:] if len(tok) > 12 else "***"
        if cfg.get("discord", {}).get("webhook_url"):
            url = cfg["discord"]["webhook_url"]
            cfg["discord"]["webhook_url"] = url[:40] + "…" if len(url) > 50 else "***"
        if cfg.get("auth", {}).get("password_hash"):
            cfg["auth"].pop("password_hash", None)
            cfg["auth"]["has_password"] = True
        if cfg.get("auth", {}).get("session_secret"):
            cfg["auth"].pop("session_secret", None)
        return jsonify({"ok": True, "config": cfg})

    @app.route("/api/apply", methods=["POST"])
    def api_apply():
        """Tell the running collector to re-read its config without restart.

        Writes a small 'reload' trigger file the collector polls, and also
        sends SIGHUP if the collector PID is known.
        """
        try:
            # Touch a sentinel file; the collector watches mtime of the config file
            cfg = load_config()
            path = save_config(cfg)
            os.utime(path, None)  # bump mtime
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        # Also try SIGHUP on collector via systemd-run or direct pid file
        pidfile = Path("/tmp/systor-collector.pid")
        sig_sent = False
        if pidfile.exists():
            try:
                pid = int(pidfile.read_text().strip())
                os.kill(pid, signal.SIGHUP)
                sig_sent = True
            except (ValueError, ProcessLookupError, PermissionError):
                pass
        return jsonify({
            "ok": True,
            "message": "Config saved. Collector will pick up changes on its next loop iteration (within "
                       + str(cfg["collector"]["poll_interval_sec"]) + "s)."
                       + (" (SIGHUP sent)" if sig_sent else ""),
            "config_path": str(path),
        })

    @app.route("/api/restart-collector", methods=["POST"])
    def api_restart_collector():
        """Restart collector whether it is pidfile-managed or just a bare process."""
        pidfile = Path("/tmp/systor-collector.pid")
        pid = None
        started_fallback = False

        if pidfile.exists():
            try:
                pid = int(pidfile.read_text().strip())
            except ValueError:
                pid = None
        def _collector_pids() -> list[int]:
            try:
                out = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=3).stdout.splitlines()
                pids = []
                for line in out:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(None, 1)
                    if len(parts) != 2:
                        continue
                    pid_txt, args = parts
                    if args.strip() in ("python3 -m systor serve collector", f"{sys.executable} -m systor serve collector"):
                        try:
                            pids.append(int(pid_txt))
                        except ValueError:
                            pass
                return pids
            except Exception:
                return []

        if pid is None:
            pids = [p for p in _collector_pids() if p != os.getpid()]
            if pids:
                pid = pids[0]

        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pid = None

        # Give the old collector a moment to exit, then check whether another
        # collector came back. If not, start a detached replacement so the
        # button works in manual/non-systemd deployments.
        live = []
        for _ in range(8):
            try:
                live = _collector_pids()
            except Exception:
                live = []
            if pid is None:
                break
            pid_still_live = pid in live
            if not pid_still_live:
                break
            time.sleep(0.35)
        live = [p for p in live if pid is None or p != pid]
        if not live:
            try:
                subprocess.Popen(
                    [sys.executable, "-m", "systor", "serve", "collector"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
                started_fallback = True
            except Exception as e:
                return jsonify({"ok": False, "error": f"collector stopped but fallback start failed: {e}"}), 500

        if pid is None and not started_fallback:
            return jsonify({"ok": False, "error": "collector not found"}), 404
        return jsonify({
            "ok": True,
            "message": (
                f"Collector restart triggered for PID {pid}."
                if pid is not None else "Collector started."
            ) + (" Fallback start used." if started_fallback else "")
        })

    @app.route("/api/restart-web", methods=["POST"])
    def api_restart_web():
        runtime = _runtime_status()
        current_pid = runtime.get("web", {}).get("pid")
        host = runtime.get("web", {}).get("host", "0.0.0.0")
        port = runtime.get("web", {}).get("port", 6677)
        workdir = str(Path(__file__).resolve().parents[1])
        if not current_pid:
            return jsonify({"ok": False, "error": "web process not found"}), 404
        script = (
            f"cd {workdir} && "
            f"sleep 1 && "
            f"kill -9 {current_pid} >/dev/null 2>&1 || true && "
            f"nohup {sys.executable} -m systor serve web >/dev/null 2>&1 &"
        )
        try:
            subprocess.Popen(["bash", "-lc", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)
            return jsonify({"ok": True, "message": f"Web restart scheduled for PID {current_pid} on {host}:{port}. Refresh this page in 2-3 seconds."})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/restart-all", methods=["POST"])
    def api_restart_all():
        collector = api_restart_collector().get_json()
        web = api_restart_web().get_json()
        ok = bool(collector.get("ok")) and bool(web.get("ok"))
        code = 200 if ok else 500
        return jsonify({"ok": ok, "collector": collector, "web": web}), code

    @app.route("/logs/raw")
    @app.route("/api/logs")
    def api_logs_raw():
        cfg = load_config()
        log_file = cfg.get("logging", {}).get("file", "/var/log/systor/systor.log")
        try:
            lines = int(request.args.get("lines", request.args.get("limit", 200)))
        except (TypeError, ValueError):
            lines = 200
        lines = max(1, min(2000, lines))
        level = request.args.get("level", "all")
        data = read_log_tail(log_file, lines)
        if level == "errors":
            data = [x for x in data if any(k in x for k in ("ERROR", "Traceback", "OperationalError", "Exception"))]
        elif level == "warnings":
            data = [x for x in data if any(k in x for k in ("WARNING", "ERROR", "Traceback", "OperationalError", "Exception"))]
        return jsonify({
            "ok": True,
            "path": log_file,
            "lines": data,
            "total": len(data),
            "fresh_error_count": count_recent_log_errors(log_file, minutes=10),
        })

    @app.route("/api/logs/clear", methods=["POST"])
    def api_logs_clear():
        cfg = load_config()
        log_file = cfg.get("logging", {}).get("file", "/var/log/systor/systor.log")
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            Path(log_file).write_text("")
            return jsonify({"ok": True, "message": f"Cleared {log_file}"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/logs/download")
    def logs_download():
        cfg = load_config()
        log_file = cfg.get("logging", {}).get("file", "/var/log/systor/systor.log")
        p = Path(log_file)
        if not p.exists():
            return Response("log file not found\n", status=404, mimetype="text/plain")
        return Response(
            p.read_text(errors="replace"),
            mimetype="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{p.name}"'},
        )

    @app.route("/health")
    def health():
        return jsonify({"ok": True, "ts": time.time()})

    return app


def count_recent_log_errors(path: str, minutes: int = 10) -> int:
    """Best-effort recent error count for the Logs page status chip."""
    import datetime as _dt
    cutoff = _dt.datetime.now() - _dt.timedelta(minutes=minutes)
    count = 0
    for line in read_log_tail(path, 2000):
        if not any(k in line for k in ("ERROR", "Traceback", "OperationalError", "Exception")):
            continue
        try:
            ts = _dt.datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if ts >= cutoff:
            count += 1
    return count


def read_log_tail(path: str, lines: int = 200) -> list[str]:
    """Read the last N lines of a log file. Returns [] on any error."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        # Use simple tail by reading from end (efficient for moderate files)
        size = p.stat().st_size
        if size == 0:
            return []
        chunk = min(size, 64 * 1024)
        with p.open("rb") as f:
            f.seek(max(0, size - chunk))
            data = f.read().decode(errors="replace")
        all_lines = data.splitlines()
        return all_lines[-lines:]
    except Exception:
        return []


def run():
    cfg = load_config()
    log_cfg = cfg.get("logging", {})
    logging.basicConfig(
        level=getattr(logging, log_cfg.get("level", "INFO")),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    web_cfg = cfg.get("web", {})
    host = web_cfg.get("host", "127.0.0.1")
    port = int(web_cfg.get("port", 6677))

    log.info("web: starting on %s:%d", host, port)
    app = create_app()

    # Use waitress if available (production-quality WSGI, pure Python, low memory)
    try:
        from waitress import serve
        log.info("web: using waitress")
        serve(app, host=host, port=port, threads=2, ident="systor")
    except ImportError:
        log.info("web: waitress not installed, falling back to flask dev server")
        app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    run()
