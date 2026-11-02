"""Dashboard blueprint for Systor: UI, metrics series, snapshot, and health."""
from __future__ import annotations
import hashlib
import json
import time
import subprocess
from flask import Blueprint, render_template, request, jsonify, Response

from ..config import load_config
from ..metrics import collect_snapshot
from .. import __version__, __app_name__
from .common import (
    get_storage, parse_hours_arg, bucket_series_points, bucket_network_points
)

dashboard_bp = Blueprint("dashboard", __name__)

_public_dash_cache: dict = {"ts": 0.0, "hours": None, "body": b"", "etag": ""}


@dashboard_bp.route("/", endpoint="page_dashboard")
def page_dashboard():
    cfg = load_config()
    return render_template("dashboard.html", cfg=cfg)


def _clean_config(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        return cfg
    import copy
    c = copy.deepcopy(cfg)
    for section in ("telegram", "discord", "auth", "storage_page"):
        if section in c and isinstance(c[section], dict):
            for secret_key in ("bot_token", "webhook_url", "password_hash", "session_secret", "action_password_hash"):
                if secret_key in c[section] and c[section][secret_key]:
                    c[section][secret_key] = "********"
    return c


@dashboard_bp.route("/health")
def health():
    cfg = load_config()
    poll_sec = int(cfg.get("collector", {}).get("poll_interval_sec", 2))
    try:
        st = get_storage().stats()
        newest = st.get("newest_sample_ts")
        now = time.time()
        if st.get("samples", 0) > 0 and newest is not None:
            age = now - newest
            if age > (3 * poll_sec):
                return jsonify({
                    "ok": False,
                    "error": f"Stale collector data: newest sample is {age:.1f}s old (limit {3 * poll_sec}s)",
                    "newest_sample_ts": newest,
                    "age_sec": round(age, 1),
                    "ts": now
                }), 503
        return jsonify({"ok": True, "ts": now, "db_ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": f"Database unreachable: {e}", "ts": time.time()}), 503


@dashboard_bp.route("/api/version")
def api_version():
    return jsonify({"name": __app_name__, "version": __version__})


@dashboard_bp.route("/api/snapshot")
def api_snapshot():
    s = collect_snapshot()
    s["db_stats"] = get_storage().stats()
    body = jsonify(s).get_data()
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


@dashboard_bp.route("/api/series")
def api_series():
    metric = request.args.get("metric", "cpu_pct")
    hours = parse_hours_arg(request.args.get("hours"), 6.0)
    data = get_storage().series(metric, hours=hours)
    data = bucket_series_points(data, 600)
    return jsonify({"metric": metric, "hours": hours, "data": data})


@dashboard_bp.route("/api/system")
def api_system():
    cfg = load_config()
    return jsonify({
        "config": _clean_config(cfg),
        "snapshot": collect_snapshot(),
        "db_stats": get_storage().stats(),
    })


@dashboard_bp.route("/api/public-dashboard")
def api_public_dashboard():
    global _public_dash_cache
    hours = parse_hours_arg(request.args.get("hours"), 6.0)
    now = time.time()
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
    storage = get_storage()
    metrics = {
        "cpu": bucket_series_points(storage.series("cpu_pct", hours=hours), 600),
        "temp": bucket_series_points(storage.series("cpu_temp", hours=hours), 600),
        "mem": bucket_series_points(storage.series("mem_used_mb", hours=hours), 600),
        "disk_read": bucket_series_points(storage.series("disk_read_mbps", hours=hours), 600),
        "disk_write": bucket_series_points(storage.series("disk_write_mbps", hours=hours), 600),
        "load1": bucket_series_points(storage.series("load_1m", hours=hours), 600),
        "load5": bucket_series_points(storage.series("load_5m", hours=hours), 600),
        "load15": bucket_series_points(storage.series("load_15m", hours=hours), 600),
    }
    net = bucket_network_points(storage.network_series(hours=hours), 600)
    body = json.dumps({"ok": True, "hours": hours, "snapshot": snap, "metrics": metrics, "network": net}, separators=(",", ":")).encode()
    etag = '"' + hashlib.md5(body).hexdigest()[:16] + '"'
    _public_dash_cache = {"ts": now, "hours": hours, "body": body, "etag": etag}

    if request.headers.get("If-None-Match") == etag:
        resp = Response(status=304)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "public, max-age=2"
        return resp
    resp = Response(body, mimetype="application/json")
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "public, max-age=2"
    return resp


@dashboard_bp.route("/api/access")
def api_access():
    cfg = load_config()
    host = cfg.get("web", {}).get("host", "0.0.0.0")
    port = int(cfg.get("web", {}).get("port", 6677))
    lan_ips = []
    try:
        all_ipv4 = [x for x in subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=3).stdout.split() if "." in x]
        preferred = ""
        try:
            route_probe = subprocess.run(
                ["bash", "-lc", "ip -4 route get 1.1.1.1 | sed -n 's/.* src \\([^ ]*\\).*/\\1/p'"],
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout.strip()
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


@dashboard_bp.route("/metrics")
def prometheus_metrics():
    """Prometheus OpenMetrics exposition format."""
    from .common import client_private
    # Private client or valid bearer token
    cfg = load_config()
    prom_token = cfg.get("metrics", {}).get("token", "")
    auth_header = request.headers.get("Authorization", "")
    if prom_token and auth_header == f"Bearer {prom_token}":
        pass
    elif not client_private():
        return Response("Forbidden", status=403, mimetype="text/plain")

    snap = collect_snapshot()
    cpu = snap.get("cpu", {})
    mem = snap.get("memory", {})
    disk_io = snap.get("disk_io", {})

    lines: list[str] = [
        "# HELP systor_uptime_seconds System uptime in seconds",
        "# TYPE systor_uptime_seconds counter",
        f"systor_uptime_seconds {snap.get('uptime_sec') or 0}",
    ]

    # CPU
    if cpu.get("percent") is not None:
        lines.extend([
            "# HELP systor_cpu_percent Current overall CPU usage percentage",
            "# TYPE systor_cpu_percent gauge",
            f"systor_cpu_percent {cpu['percent']}",
        ])
    if cpu.get("load_1m") is not None:
        lines.extend([
            "# HELP systor_cpu_load System load average",
            "# TYPE systor_cpu_load gauge",
            f'systor_cpu_load{{period="1m"}} {cpu["load_1m"]}',
            f'systor_cpu_load{{period="5m"}} {cpu.get("load_5m") or 0.0}',
            f'systor_cpu_load{{period="15m"}} {cpu.get("load_15m") or 0.0}',
        ])
    if cpu.get("temp_c") is not None:
        lines.extend([
            "# HELP systor_cpu_temperature_celsius Maximum CPU thermal sensor temperature",
            "# TYPE systor_cpu_temperature_celsius gauge",
            f"systor_cpu_temperature_celsius {cpu['temp_c']}",
        ])

    # Memory
    if mem.get("total_mb"):
        lines.extend([
            "# HELP systor_memory_bytes Host memory statistics in bytes",
            "# TYPE systor_memory_bytes gauge",
            f'systor_memory_bytes{{state="total"}} {mem["total_mb"] * 1024 * 1024}',
            f'systor_memory_bytes{{state="used"}} {mem.get("used_mb", 0) * 1024 * 1024}',
            f'systor_memory_bytes{{state="free"}} {mem.get("free_mb", 0) * 1024 * 1024}',
            f'systor_memory_bytes{{state="available"}} {mem.get("available_mb", 0) * 1024 * 1024}',
        ])
    if mem.get("swap_total_mb"):
        lines.extend([
            "# HELP systor_swap_bytes Host swap statistics in bytes",
            "# TYPE systor_swap_bytes gauge",
            f'systor_swap_bytes{{state="total"}} {mem["swap_total_mb"] * 1024 * 1024}',
            f'systor_swap_bytes{{state="used"}} {mem.get("swap_used_mb", 0) * 1024 * 1024}',
        ])

    # Disks
    disks = snap.get("disks") or []
    if disks:
        lines.extend([
            "# HELP systor_disk_used_percent Percentage of disk capacity used",
            "# TYPE systor_disk_used_percent gauge",
        ])
        for d in disks:
            mount = d.get("mount", "/")
            pct = d.get("used_pct", 0)
            lines.append(f'systor_disk_used_percent{{mount="{mount}"}} {pct}')

    # Disk IO
    lines.extend([
        "# HELP systor_disk_io_rate_mbps Real-time disk throughput in MB/s",
        "# TYPE systor_disk_io_rate_mbps gauge",
        f'systor_disk_io_rate_mbps{{direction="read"}} {disk_io.get("read_mbps", 0.0)}',
        f'systor_disk_io_rate_mbps{{direction="write"}} {disk_io.get("write_mbps", 0.0)}',
    ])

    # Network Interfaces
    ifaces = snap.get("network_interfaces") or []
    if ifaces:
        lines.extend([
            "# HELP systor_network_traffic_bytes_total Cumulative network bytes transferred",
            "# TYPE systor_network_traffic_bytes_total counter",
        ])
        for iface in ifaces:
            iname = iface.get("iface", "unknown")
            rx = iface.get("rx_bytes", 0)
            tx = iface.get("tx_bytes", 0)
            lines.append(f'systor_network_traffic_bytes_total{{iface="{iname}",direction="rx"}} {rx}')
            lines.append(f'systor_network_traffic_bytes_total{{iface="{iname}",direction="tx"}} {tx}')

    # Service Uptime Targets
    uptime_summary = get_storage().get_uptime_summary()
    if uptime_summary:
        lines.extend([
            "# HELP systor_service_uptime_up Status of monitored services (1=up, 0=down)",
            "# TYPE systor_service_uptime_up gauge",
        ])
        for t in uptime_summary:
            tname = t.get("name", "target").replace('"', '\\"')
            turl = t.get("url", "").replace('"', '\\"')
            is_up = 1 if (t.get("last_check") and t["last_check"].get("ok")) else 0
            lines.append(f'systor_service_uptime_up{{name="{tname}",url="{turl}"}} {is_up}')

    body = "\n".join(lines) + "\n"
    return Response(body, mimetype="text/plain; version=0.0.4; charset=utf-8")

