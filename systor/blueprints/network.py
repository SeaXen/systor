"""Network telemetry blueprint for Systor: traffic series, interface stats, and usage."""
from __future__ import annotations
from flask import Blueprint, render_template, request, jsonify

from ..config import load_config
from ..metrics import collect_snapshot, read_network_interfaces
from .common import get_storage, parse_hours_arg, bucket_network_points

network_bp = Blueprint("network", __name__)


@network_bp.route("/network", endpoint="page_network")
def page_network():
    cfg = load_config()
    return render_template("network.html", cfg=cfg)


@network_bp.route("/api/network-series")
def api_network_series():
    hours = parse_hours_arg(request.args.get("hours"), 24.0)
    data = get_storage().network_series(hours=hours)
    data = bucket_network_points(data, 600)
    snap = collect_snapshot()
    return jsonify({"ok": True, "hours": hours, "data": data, "current": snap.get("network", {})})


@network_bp.route("/api/network-usage")
def api_network_usage():
    granularity = request.args.get("granularity", "day")
    try:
        limit = max(1, min(5000, int(request.args.get("limit", 30))))
    except (ValueError, TypeError):
        limit = 30
    iface = (request.args.get("iface") or "all").strip()
    data = get_storage().network_usage_buckets(granularity=granularity, limit=limit, iface=iface)
    return jsonify({
        "ok": True,
        "granularity": granularity,
        "limit": limit,
        "iface": iface,
        "data": data,
        "stats": get_storage().stats()
    })


@network_bp.route("/api/network-interfaces")
def api_network_interfaces():
    rows = read_network_interfaces()
    top_rx = max(rows, key=lambda r: r.get("rx_mbps", 0), default=None)
    top_tx = max(rows, key=lambda r: r.get("tx_mbps", 0), default=None)
    top_total = max(rows, key=lambda r: r.get("total_bytes", 0), default=None)
    return jsonify({
        "ok": True,
        "interfaces": rows,
        "top_rx": top_rx,
        "top_tx": top_tx,
        "top_total": top_total
    })


# --- Service Uptime & Blackbox Health Endpoints ---
@network_bp.route("/api/uptime", methods=["GET"])
def api_uptime_list():
    try:
        hours = max(1, min(720, int(request.args.get("hours", 24))))
    except (ValueError, TypeError):
        hours = 24
    summary = get_storage().get_uptime_summary(hours=hours)
    return jsonify({"ok": True, "hours": hours, "targets": summary, "count": len(summary)})


@network_bp.route("/api/uptime/targets", methods=["POST"])
def api_uptime_create():
    from .common import client_private, auth_enabled, auth_logged_in
    if not client_private():
        return jsonify({"ok": False, "error": "Uptime configuration is private LAN only"}), 403
    if auth_enabled() and not auth_logged_in():
        return jsonify({"ok": False, "error": "Admin login required"}), 401

    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    url = str(body.get("url") or body.get("target") or "").strip()
    if not name or not url:
        return jsonify({"ok": False, "error": "Target name and URL are required"}), 400

    method = str(body.get("method") or body.get("check_type") or "GET").upper()
    try:
        expected = int(body.get("expected_status") or 200)
        interval = max(10, int(body.get("interval_sec") or 60))
        timeout = max(1.0, float(body.get("timeout_sec") or 5.0))
    except (ValueError, TypeError):
        expected, interval, timeout = 200, 60, 5.0

    target_id = get_storage().add_uptime_target(name, url, method, expected, interval, timeout)
    # Ping immediately upon creation
    from ..services.uptime_service import ping_target
    target = get_storage().get_uptime_target(target_id)
    if target:
        ok, code, lat, err = ping_target(target)
        get_storage().record_uptime_check(target_id, code, lat, ok, err)

    return jsonify({"ok": True, "id": target_id, "message": f"Uptime target '{name}' created"})


@network_bp.route("/api/uptime/targets", methods=["DELETE"])
@network_bp.route("/api/uptime/targets/<int:tid>", methods=["PUT", "DELETE"])
def api_uptime_modify(tid: int | None = None):
    from .common import client_private, auth_enabled, auth_logged_in
    if not client_private():
        return jsonify({"ok": False, "error": "Uptime configuration is private LAN only"}), 403
    if auth_enabled() and not auth_logged_in():
        return jsonify({"ok": False, "error": "Admin login required"}), 401

    storage = get_storage()
    if tid is None:
        try:
            tid = int(request.args.get("id", (request.get_json(silent=True) or {}).get("id", 0)))
        except (ValueError, TypeError):
            tid = 0

    if not tid:
        return jsonify({"ok": False, "error": "Target ID is required"}), 400

    if request.method == "DELETE":
        ok = storage.delete_uptime_target(tid)
        return jsonify({"ok": ok, "message": "Target deleted" if ok else "Target not found"})

    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    url = str(body.get("url") or body.get("target") or "").strip()
    if not name or not url:
        return jsonify({"ok": False, "error": "Name and URL are required"}), 400

    method = str(body.get("method") or body.get("check_type") or "GET").upper()
    try:
        expected = int(body.get("expected_status") or 200)
        interval = max(10, int(body.get("interval_sec") or 60))
        timeout = max(1.0, float(body.get("timeout_sec") or 5.0))
        enabled = bool(body.get("enabled", True))
    except (ValueError, TypeError):
        expected, interval, timeout, enabled = 200, 60, 5.0, True

    ok = storage.update_uptime_target(tid, name, url, method, expected, interval, timeout, enabled)
    return jsonify({"ok": ok, "message": "Target updated" if ok else "Target not found"})


@network_bp.route("/api/uptime/check-now", methods=["POST"])
def api_uptime_check_now():
    from ..services.uptime_service import run_single_audit_cycle
    storage = get_storage()
    # Force check for all enabled targets
    targets = storage.list_uptime_targets(enabled_only=True)
    from ..services.uptime_service import ping_target
    for t in targets:
        ok, code, lat, err = ping_target(t)
        storage.record_uptime_check(t["id"], code, lat, ok, err)
    return jsonify({"ok": True, "checked": len(targets)})


# --- SSL / TLS Certificate Expiry Endpoints ---
@network_bp.route("/api/ssl/certs", methods=["GET"])
def api_ssl_certs():
    cfg = load_config()
    custom_targets = cfg.get("ssl", {}).get("targets", [])
    if isinstance(custom_targets, str):
        custom_targets = [x.strip() for x in custom_targets.split(",") if x.strip()]
    query_target = request.args.get("host")
    if query_target:
        custom_targets.append(query_target.strip())

    from ..services.ssl_service import get_ssl_certificates
    certs = get_ssl_certificates(custom_targets=custom_targets)
    return jsonify({"ok": True, "certs": certs, "certificates": certs, "count": len(certs)})


