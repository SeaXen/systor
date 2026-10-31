"""Apps monitoring blueprint for Systor: Host and Docker workloads."""
from __future__ import annotations
import time
from flask import Blueprint, render_template, request, jsonify

from ..config import load_config
from ..metrics import read_top_processes, read_total_memory_mb
from ..services.runtime_service import (
    get_host_apps, get_docker_apps, get_cached_apps, set_cached_apps
)

apps_bp = Blueprint("apps", __name__)


@apps_bp.route("/apps", endpoint="page_apps")
def page_apps():
    cfg = load_config()
    return render_template("apps.html", cfg=cfg)


@apps_bp.route("/api/top-processes")
def api_top_processes():
    try:
        n = max(1, min(50, int(request.args.get("n", 10))))
    except ValueError:
        n = 10
    by = request.args.get("by", "cpu")
    if by not in ("cpu", "mem"):
        by = "cpu"
    procs = read_top_processes(n=n, by=by)
    total_mb = read_total_memory_mb()
    for p in procs:
        p["mem_percent"] = round(100.0 * p["mem_mb"] / total_mb, 1) if total_mb else 0.0
    return jsonify({"by": by, "n": len(procs), "processes": procs, "total_memory_mb": total_mb})


@apps_bp.route("/api/apps")
def api_apps():
    scope = request.args.get("scope", "all")
    sort_by = request.args.get("sort", "cpu")
    try:
        limit = max(1, min(1000, int(request.args.get("limit", 24))))
    except (ValueError, TypeError):
        limit = 24

    now = time.time()
    fetch_limit = max(limit, 96)
    cached = get_cached_apps()
    cached_ts = float(cached.get("ts") or 0)
    cached_limit = int(cached.get("limit") or 0)
    cached_host = list(cached.get("host_rows") or [])
    cached_docker = list(cached.get("docker_rows") or [])

    if cached_ts > 0 and now - cached_ts < 6.0 and cached_limit >= limit and (cached_host or cached_docker):
        host_rows = cached_host[:limit]
        docker_rows = cached_docker[:limit]
    else:
        host_all = get_host_apps(limit=fetch_limit)
        docker_all = get_docker_apps(limit=fetch_limit)
        set_cached_apps(now, fetch_limit, host_all, docker_all)
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
