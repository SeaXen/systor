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
import sys
import threading
import time
import subprocess
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, render_template, request, abort, Response

from .config import load_config, save_config
from .metrics import collect_snapshot, read_top_processes, read_total_memory_mb, read_network_interfaces
from .notifier import Notifier, send_telegram, send_discord
from .storage import Storage, DEFAULT_DB_PATH
from .speed import run_provider, run_many, iperf_status, start_iperf_server, local_ipv4s

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
        if float(_docker_cache.get("ts") or 0) > 0 and now - float(_docker_cache.get("ts") or 0) < 5:
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
    cpu_line = f"🧠 CPU {cpu.get('percent', '?')}% · load {cpu.get('load_1m', '?')}"
    ram_line = f"🧮 RAM free {mem.get('available_mb', '?')} MB"
    disk_line = f"💽 Disk {worst_disk.get('mount', '?')} {worst_disk.get('used_pct', '?')}%" if worst_disk else ""
    app_line = f"🔥 {top_cpu.get('name')} {top_cpu.get('cpu_percent', 0)}% CPU" if top_cpu.get('name') else ""
    if channel == 'telegram':
        lines = [f"🧪 <b>Systor Telegram test</b>", f"🏷️ {host}", cpu_line, ram_line]
        if disk_line:
            lines.append(disk_line)
        if app_line:
            lines.append(app_line)
        return "\n".join(lines)
    lines = ["🧪 **Systor Discord test**", f"🏷️ **{host}**", cpu_line, ram_line]
    if disk_line:
        lines.append(disk_line)
    if app_line:
        lines.append(app_line)
    return "\n".join(lines)


def _looks_masked_secret(value: str) -> bool:
    value = (value or "").strip()
    return bool(value) and ("***" in value or "…" in value)


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["JSON_SORT_KEYS"] = False

    # ---------- API: live snapshot ----------
    @app.route("/api/snapshot")
    def api_snapshot():
        s = collect_snapshot()
        s["db_stats"] = get_storage().stats()
        return jsonify(s)

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
        limit = max(1, min(500, int(request.args.get("limit", 50))))
        provider = (request.args.get("provider") or "all").strip().lower()
        rows = get_storage().recent_speedtests(limit=limit, provider=provider)
        return jsonify({"ok": True, "rows": rows, "provider": provider, "limit": limit})

    @app.route("/api/speed/status")
    def api_speed_status():
        cfg = load_config()
        rows = get_storage().recent_speedtests(limit=200, provider="all")
        latest = []
        seen = set()
        for row in rows:
            prov = row.get("provider")
            if prov in seen:
                continue
            seen.add(prov)
            latest.append(row)
        port = int((cfg.get("speed", {}) or {}).get("iperf_port", 5201))
        return jsonify({"ok": True, "latest": latest, "iperf": iperf_status(port), "local_ips": local_ipv4s(), "config": cfg})

    @app.route("/api/speed/run", methods=["POST"])
    def api_speed_run():
        cfg = load_config()
        body = request.get_json(silent=True) or {}
        provider = str(body.get("provider") or "speedtest").strip().lower()
        providers = ["speedtest", "librespeed", "notion", "cloudflare"] if provider == "all" else [provider]
        rows = run_many(providers, cfg=cfg)
        for row in rows:
            get_storage().log_speedtest(row)
        return jsonify({"ok": True, "rows": rows})

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
        host_rows = _host_apps(limit=limit)
        docker_rows = _docker_apps(limit=limit)
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
                try: cfg["speed"]["page_refresh_sec"] = max(5, int(data["speed_page_refresh_sec"]))
                except (ValueError, TypeError): pass
            if "speed_default_provider" in data and data["speed_default_provider"] in ("speedtest", "librespeed", "notion", "cloudflare"):
                cfg["speed"]["default_provider"] = data["speed_default_provider"]
            if "speed_auto_enabled" in data:
                cfg["speed"]["auto_enabled"] = _bool(data.get("speed_auto_enabled"))
            if "speed_auto_provider" in data and data["speed_auto_provider"] in ("speedtest", "librespeed", "notion", "cloudflare"):
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
            if "speed_iperf_port" in data and data["speed_iperf_port"]:
                try: cfg["speed"]["iperf_port"] = max(1, min(65535, int(data["speed_iperf_port"])))
                except (ValueError, TypeError): pass
            # Update web (host/port)
            if "web_host" in data and data["web_host"]:
                cfg["web"]["host"] = data["web_host"]
            if "web_port" in data and data["web_port"]:
                try: cfg["web"]["port"] = int(data["web_port"])
                except (ValueError, TypeError): pass
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
