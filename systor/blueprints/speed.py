"""Speedtest runner blueprint for Systor."""
from __future__ import annotations
import json
import time
from flask import Blueprint, render_template, request, jsonify

from ..config import load_config
from ..speed import (
    run_many, iperf_status, start_iperf_server, local_ipv4s,
    list_ookla_servers, list_librespeed_servers
)
from ..services.speed_service import (
    speed_live_copy, speed_live_reset, speed_live_worker,
    stop_speed_live, start_speed_live
)
from .common import get_storage

speed_bp = Blueprint("speed", __name__)


@speed_bp.route("/speed", endpoint="page_speed")
def page_speed():
    cfg = load_config()
    return render_template("speed.html", cfg=cfg)


@speed_bp.route("/api/speedtests")
def api_speedtests():
    cfg = load_config()
    default_limit = int((cfg.get("speed", {}) or {}).get("history_page_size", 25))
    try:
        limit = max(1, min(500, int(request.args.get("limit", default_limit))))
        offset = max(0, int(request.args.get("offset", 0)))
    except (ValueError, TypeError):
        limit = default_limit
        offset = 0
    provider = (request.args.get("provider") or "all").strip().lower()
    run_type = (request.args.get("run_type") or "all").strip().lower()
    rows, total = get_storage().recent_speedtests(limit=limit, offset=offset, provider=provider, run_type=run_type)
    return jsonify({
        "ok": True, "rows": rows, "provider": provider,
        "run_type": run_type, "limit": limit, "offset": offset, "total": total
    })


@speed_bp.route("/api/speed/status")
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
    return jsonify({
        "ok": True,
        "latest": latest,
        "iperf": iperf_status(port),
        "local_ips": local_ipv4s(),
        "config": cfg,
        "live": speed_live_copy()
    })


@speed_bp.route("/api/speed/live/start", methods=["POST"])
def api_speed_live_start():
    body = request.get_json(silent=True) or {}
    server_id = str(body.get("server_id") or "").strip()
    target = str(body.get("target") or "").strip()
    run_type = str(body.get("run_type") or "manual").strip().lower()

    def on_success(row):
        get_storage().log_speedtest(row)

    started = start_speed_live(server_id=server_id, run_type=run_type, on_success=on_success)
    if not started:
        return jsonify({"ok": False, "error": "speedtest already running", "live": speed_live_copy()}), 409
    time.sleep(0.2)
    return jsonify({"ok": True, "live": speed_live_copy()})


@speed_bp.route("/api/speed/live/status")
def api_speed_live_status():
    return jsonify({"ok": True, "live": speed_live_copy()})


@speed_bp.route("/api/speed/live/stop", methods=["POST"])
def api_speed_live_stop():
    stop_speed_live()
    return jsonify({"ok": True, "live": speed_live_copy()})


@speed_bp.route("/api/speed/options")
def api_speed_options():
    return jsonify({
        "ok": True,
        "ookla_servers": list_ookla_servers(limit=20),
        "librespeed_servers": list_librespeed_servers(limit=40),
    })


@speed_bp.route("/api/speed/run", methods=["POST"])
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


@speed_bp.route("/api/speed/log", methods=["POST"])
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


@speed_bp.route("/api/speed/iperf/start", methods=["POST"])
def api_speed_iperf_start():
    cfg = load_config()
    port = int((cfg.get("speed", {}) or {}).get("iperf_port", 5201))
    st = start_iperf_server(port)
    return jsonify({"ok": True, "status": st})
