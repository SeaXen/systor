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
