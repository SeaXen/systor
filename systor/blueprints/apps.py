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


# --- Systemd Service Management Endpoints ---
@apps_bp.route("/api/apps/systemd", methods=["GET"])
def api_systemd_list():
    from ..services.systemd_service import list_systemd_services
    data = list_systemd_services()
    return jsonify({"ok": True, **data})


@apps_bp.route("/api/apps/systemd/<path:unit>/restart", methods=["POST"])
def api_systemd_restart(unit: str):
    from .common import client_private, auth_enabled, auth_logged_in, get_storage
    from ..security import get_client_ip, parse_trusted_proxies
    from ..config import load_config
    if not client_private():
        return jsonify({"ok": False, "error": "Systemd service control is private LAN only"}), 403
    if auth_enabled() and not auth_logged_in():
        return jsonify({"ok": False, "error": "Admin login required to control services"}), 401

    cfg = load_config()
    trusted = parse_trusted_proxies(cfg.get("web", {}).get("trusted_proxies", "127.0.0.1, ::1"))
    client_ip = get_client_ip(request, trusted)
    from flask import session
    user = session.get("systor_user", "admin")

    from ..services.systemd_service import restart_systemd_service
    storage = get_storage()
    ok, msg = restart_systemd_service(unit, user, client_ip, storage)
    status = 200 if ok else 400
    return jsonify({"ok": ok, "message": msg, "unit": unit}), status


@apps_bp.route("/api/apps/systemd/audit", methods=["GET"])
def api_systemd_audit():
    from .common import get_storage
    try:
        limit = max(1, min(200, int(request.args.get("limit", 50))))
    except (ValueError, TypeError):
        limit = 50
    rows = get_storage().recent_systemd_actions(limit=limit)
    return jsonify({"ok": True, "actions": rows, "count": len(rows)})


# --- Docker Compose Stacks Endpoints ---
@apps_bp.route("/api/apps/compose", methods=["GET"])
def api_apps_compose():
    from ..services.runtime_service import get_compose_stacks
    stacks = get_compose_stacks()
    return jsonify({"ok": True, "stacks": stacks, "count": len(stacks)})


@apps_bp.route("/api/apps/compose/<project>/restart", methods=["POST"])
def api_apps_compose_restart(project: str):
    from .common import client_private, auth_enabled, auth_logged_in
    if not client_private():
        return jsonify({"ok": False, "error": "Docker Compose control is private LAN only"}), 403
    if auth_enabled() and not auth_logged_in():
        return jsonify({"ok": False, "error": "Admin login required"}), 401

    from ..services.runtime_service import restart_compose_project
    ok, msg = restart_compose_project(project)
    status = 200 if ok else 400
    return jsonify({"ok": ok, "message": msg, "project": project}), status


# --- Live Service & Container Log Streamer (SSE) ---
@apps_bp.route("/api/apps/logs/stream")
def api_apps_logs_stream():
    import json
    import re
    import shutil
    import subprocess
    from flask import Response, stream_with_context
    from .common import client_private
    if not client_private():
        return jsonify({"ok": False, "error": "Log streaming is private LAN only"}), 403

    unit = request.args.get("unit", "").strip()
    container_id = request.args.get("container", "").strip()
    try:
        lines = min(200, max(10, int(request.args.get("lines", 50))))
    except (ValueError, TypeError):
        lines = 50

    if unit:
        if not re.match(r"^[a-zA-Z0-9_\-\.@]+(\.service)?$", unit):
            return jsonify({"ok": False, "error": "Invalid service unit name"}), 400
        cmd = ["journalctl", "-u", unit, "-f", f"-n{lines}", "--no-pager"]
    elif container_id:
        if not re.match(r"^[a-zA-Z0-9_\-\.]+$", container_id):
            return jsonify({"ok": False, "error": "Invalid container ID"}), 400
        cmd = ["docker", "logs", "-f", f"--tail={lines}", container_id]
    else:
        cmd = ["journalctl", "-f", f"-n{lines}", "--no-pager"]

    def generate():
        bin_path = shutil.which(cmd[0])
        if not bin_path:
            yield f"data: {json.dumps({'error': f'{cmd[0]} not found on host system'})}\n\n"
            return
        cmd[0] = bin_path
        proc = None
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            yield f"data: {json.dumps({'status': 'connected', 'target': unit or container_id or 'system'})}\n\n"
            for line in proc.stdout:
                line_str = line.rstrip("\r\n")
                if line_str:
                    yield f"data: {json.dumps({'log': line_str})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=1)
                except Exception:
                    pass

    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


# --- Secure Web Terminal Endpoints ---
@apps_bp.route("/api/terminal/auth", methods=["POST"])
def api_terminal_auth():
    from .common import client_private
    if not client_private():
        return jsonify({"ok": False, "error": "Terminal console is private LAN / Tailscale only"}), 403

    body = request.get_json(silent=True) or {}
    password = str(body.get("password") or "").strip()
    from ..config import load_config
    from ..security import parse_trusted_proxies, get_client_ip
    cfg = load_config()
    trusted = parse_trusted_proxies(cfg.get("web", {}).get("trusted_proxies", "127.0.0.1, ::1"))
    client_ip = get_client_ip(request, trusted)
    stored_hash = cfg.get("auth", {}).get("password_hash", "")

    from ..terminal import authenticate_terminal_session
    ok, msg, token = authenticate_terminal_session(password, client_ip, stored_hash)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 401
    return jsonify({"ok": True, "token": token, "message": "Console unlocked"})


@apps_bp.route("/api/terminal/exec", methods=["POST"])
def api_terminal_exec():
    from .common import client_private
    if not client_private():
        return jsonify({"ok": False, "error": "Terminal console is private LAN / Tailscale only"}), 403

    body = request.get_json(silent=True) or {}
    token = str(body.get("token") or request.headers.get("X-Terminal-Token") or "").strip()
    command = str(body.get("command") or "").strip()

    from ..config import load_config
    from ..security import parse_trusted_proxies, get_client_ip
    cfg = load_config()
    trusted = parse_trusted_proxies(cfg.get("web", {}).get("trusted_proxies", "127.0.0.1, ::1"))
    client_ip = get_client_ip(request, trusted)

    from ..terminal import execute_console_command
    res = execute_console_command(token, client_ip, command)
    status = 200 if res.get("ok") else 400
    return jsonify(res), status

