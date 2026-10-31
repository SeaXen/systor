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


@dashboard_bp.route("/health")
def health():
    return jsonify({"ok": True, "ts": time.time()})


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
        "config": cfg,
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
