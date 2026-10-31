"""Settings, alerts, logs, and maintenance blueprint for Systor."""
from __future__ import annotations
import datetime as _dt
from datetime import datetime
import json
import logging
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from flask import Blueprint, render_template, request, jsonify, Response, current_app
from werkzeug.security import generate_password_hash

from ..config import load_config, save_config, DEFAULT_CONFIG, _yaml_to_dict
from ..metrics import collect_snapshot, read_top_processes
from ..notifier import send_telegram, send_discord
from ..services.runtime_service import get_runtime_status
from .common import (
    get_storage, client_private, auth_enabled, auth_logged_in
)

settings_bp = Blueprint("settings", __name__)
log = logging.getLogger("systor.blueprints.settings")


def _looks_masked_secret(value: str) -> bool:
    value = (value or "").strip()
    return bool(value) and ("***" in value or "…" in value)


def _config_saved_at(path: Path | None = None) -> str:
    p = path or Path("/etc/systor/config.yaml")
    try:
        ts = p.stat().st_mtime
        return datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    except Exception:
        return ""


def build_channel_test_message(channel: str) -> str:
    snap = collect_snapshot()
    cpu = snap.get("cpu", {}) or {}
    mem = snap.get("memory", {}) or {}
    disks = snap.get("disks", []) or []
    worst_disk = max(disks, key=lambda d: d.get("used_pct", 0), default={})
    top_cpu = (read_top_processes(n=1, by="cpu") or [{}])[0]
    host = snap.get("hostname", "systor")
    lines = [
        f"🖥️ {host}",
        f"🧠 CPU {cpu.get('percent', '?')}% · load {cpu.get('load_1m', '?')}",
        f"🧮 RAM free {mem.get('available_mb', '?')} MB · used {mem.get('used_mb', '?')} MB",
    ]
    if worst_disk:
        lines.append(f"💽 Disk {worst_disk.get('mount', '?')} {worst_disk.get('used_pct', '?')}%")
    if top_cpu.get("name"):
        lines.append(f"🔥 Top app {top_cpu.get('name')} · {top_cpu.get('cpu_percent', 0)}% CPU")
    prefix = "🧪 Systor Telegram test" if channel == "telegram" else "🧪 Systor Discord test"
    return "\n".join([prefix, *lines])


def find_collector_pid() -> int | None:
    for p in (Path("/var/lib/systor/collector.pid"), Path("/run/systor/collector.pid"), Path("/tmp/systor-collector.pid")):
        if p.exists():
            try:
                pid = int(p.read_text().strip())
                if pid > 0:
                    return pid
            except (ValueError, OSError):
                pass
    return None


def read_log_tail(path: str, lines: int = 200) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    try:
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


def count_recent_log_errors(path: str, minutes: int = 10) -> int:
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


@settings_bp.route("/alerts", endpoint="page_alerts")
def page_alerts():
    cfg = load_config()
    return render_template("alerts.html", cfg=cfg)


@settings_bp.route("/logs", endpoint="page_logs")
def page_logs():
    cfg = load_config()
    return render_template("logs.html", cfg=cfg)


@settings_bp.route("/api/alerts")
def api_alerts():
    try:
        limit = int(request.args.get("limit", 50))
    except (ValueError, TypeError):
        limit = 50
    return jsonify(get_storage().recent_alerts(limit=limit))


@settings_bp.route("/api/notifications")
def api_notifications():
    try:
        limit = int(request.args.get("limit", 50))
    except (ValueError, TypeError):
        limit = 50
    return jsonify(get_storage().recent_notifications(limit=limit))


@settings_bp.route("/api/runtime")
def api_runtime():
    return jsonify(get_runtime_status())


@settings_bp.route("/settings", methods=["GET", "POST"], endpoint="page_settings")
def page_settings():
    if request.method == "POST":
        cfg = load_config()
        data = request.get_json(silent=True) or request.form.to_dict()

        def _bool(v):
            return str(v).lower() in ("true", "on", "1", "yes")

        threshold_specs = [
            ("cpu_load_1m", float, True),
            ("cpu_temp_c", float, True),
            ("mem_free_mb", int, True),
            ("swap_used_mb", int, True),
            ("disk_used_pct", int, True),
        ]
        for key, cast, has_dur in threshold_specs:
            enabled_v = data.get(f"threshold_{key}_enabled")
            value_v = data.get(f"threshold_{key}_value")
            dur_v = data.get(f"threshold_{key}_duration")
            entry = cfg["thresholds"].get(key, {})
            if not isinstance(entry, dict):
                entry = {"enabled": True, "value": cast(entry) if entry is not None else 0, "duration_min": 2}
            if enabled_v is not None:
                entry["enabled"] = _bool(enabled_v)
            if value_v is not None and value_v != "":
                try:
                    entry["value"] = cast(value_v)
                except (ValueError, TypeError):
                    pass
            if has_dur and dur_v is not None and dur_v != "":
                try:
                    entry["duration_min"] = max(1, int(float(dur_v)))
                except (ValueError, TypeError):
                    pass
            cfg["thresholds"][key] = entry

        cd = data.get("cooldown_sec")
        if cd is not None and cd != "":
            try:
                cfg["thresholds"]["cooldown_sec"] = max(0, int(float(cd)))
            except (ValueError, TypeError):
                pass

        if "telegram_enabled" in data:
            cfg["telegram"]["enabled"] = _bool(data.get("telegram_enabled"))
        if "telegram_bot_token" in data and data["telegram_bot_token"]:
            tok = str(data["telegram_bot_token"]).strip()
            if not _looks_masked_secret(tok):
                cfg["telegram"]["bot_token"] = tok
        if "telegram_chat_id" in data:
            cfg["telegram"]["chat_id"] = str(data["telegram_chat_id"] or "").strip()

        if "discord_enabled" in data:
            cfg["discord"]["enabled"] = _bool(data.get("discord_enabled"))
        if "discord_webhook_url" in data and data["discord_webhook_url"]:
            cfg["discord"]["webhook_url"] = str(data["discord_webhook_url"]).strip()

        if "poll_interval_sec" in data and data["poll_interval_sec"]:
            try:
                cfg["collector"]["poll_interval_sec"] = max(1, int(data["poll_interval_sec"]))
            except (ValueError, TypeError):
                pass

        cfg.setdefault("network", {})
        if "network_default_hours" in data and data["network_default_hours"]:
            try:
                cfg["network"]["default_hours"] = max(0.25, min(24 * 3650, float(data["network_default_hours"])))
            except (ValueError, TypeError):
                pass
        if "network_auto_refresh_sec" in data and data["network_auto_refresh_sec"]:
            try:
                cfg["network"]["auto_refresh_sec"] = max(1, int(data["network_auto_refresh_sec"]))
            except (ValueError, TypeError):
                pass
        if "network_default_granularity" in data and data["network_default_granularity"] in ("day", "week", "month"):
            cfg["network"]["default_granularity"] = data["network_default_granularity"]
        if "network_default_bar_days" in data and data["network_default_bar_days"]:
            val = str(data["network_default_bar_days"]).strip()
            if val == "all":
                cfg["network"]["default_bar_days"] = "all"
            else:
                try:
                    cfg["network"]["default_bar_days"] = max(1, int(val))
                except (ValueError, TypeError):
                    pass
        if "network_default_table_days" in data and data["network_default_table_days"]:
            val = str(data["network_default_table_days"]).strip()
            if val == "all":
                cfg["network"]["default_table_days"] = "all"
            else:
                try:
                    cfg["network"]["default_table_days"] = max(1, int(val))
                except (ValueError, TypeError):
                    pass
        if "network_default_iface" in data:
            cfg["network"]["default_iface"] = str(data.get("network_default_iface") or "all").strip() or "all"
        if "network_hide_virtual_default" in data:
            cfg["network"]["hide_virtual_default"] = _bool(data.get("network_hide_virtual_default"))

        cfg.setdefault("dashboard", {})
        if "dashboard_default_hours" in data and data["dashboard_default_hours"]:
            try:
                cfg["dashboard"]["default_hours"] = max(0.25, min(24 * 3650, float(data["dashboard_default_hours"])))
            except (ValueError, TypeError):
                pass
        if "dashboard_refresh_sec" in data and data["dashboard_refresh_sec"]:
            try:
                cfg["dashboard"]["refresh_sec"] = max(1, int(data["dashboard_refresh_sec"]))
            except (ValueError, TypeError):
                pass
        if "dashboard_chart_refresh_sec" in data and data["dashboard_chart_refresh_sec"]:
            try:
                cfg["dashboard"]["chart_refresh_sec"] = max(1, int(data["dashboard_chart_refresh_sec"]))
            except (ValueError, TypeError):
                pass

        cfg.setdefault("apps", {})
        if "apps_auto_refresh_sec" in data and data["apps_auto_refresh_sec"]:
            try:
                cfg["apps"]["auto_refresh_sec"] = max(1, int(data["apps_auto_refresh_sec"]))
            except (ValueError, TypeError):
                pass
        if "apps_default_limit" in data and data["apps_default_limit"]:
            try:
                cfg["apps"]["default_limit"] = max(4, min(30, int(data["apps_default_limit"])))
            except (ValueError, TypeError):
                pass
        if "apps_default_scope" in data and data["apps_default_scope"] in ("all", "host", "docker"):
            cfg["apps"]["default_scope"] = data["apps_default_scope"]
        if "apps_default_sort" in data and data["apps_default_sort"] in ("cpu", "mem", "net", "disk"):
            cfg["apps"]["default_sort"] = data["apps_default_sort"]

        cfg.setdefault("speed", {})
        if "speed_page_refresh_sec" in data and data["speed_page_refresh_sec"]:
            try:
                cfg["speed"]["page_refresh_sec"] = max(0, int(data["speed_page_refresh_sec"]))
            except (ValueError, TypeError):
                pass
        if "speed_default_provider" in data and data["speed_default_provider"] in ("ookla", "librespeed", "notion", "cloudflare"):
            cfg["speed"]["default_provider"] = data["speed_default_provider"]
        if "speed_auto_enabled" in data:
            cfg["speed"]["auto_enabled"] = _bool(data.get("speed_auto_enabled"))
        if "speed_auto_provider" in data and data["speed_auto_provider"] in ("ookla", "librespeed", "notion", "cloudflare"):
            cfg["speed"]["auto_provider"] = data["speed_auto_provider"]
        if "speed_auto_interval_min" in data and data["speed_auto_interval_min"]:
            try:
                cfg["speed"]["auto_interval_min"] = max(5, int(float(data["speed_auto_interval_min"])))
            except (ValueError, TypeError):
                pass
        if "speed_notify_enabled" in data:
            cfg["speed"]["notify_enabled"] = _bool(data.get("speed_notify_enabled"))
        if "speed_min_download_mbps" in data and data["speed_min_download_mbps"] != "":
            try:
                cfg["speed"]["min_download_mbps"] = max(0.0, float(data["speed_min_download_mbps"]))
            except (ValueError, TypeError):
                pass
        if "speed_min_upload_mbps" in data and data["speed_min_upload_mbps"] != "":
            try:
                cfg["speed"]["min_upload_mbps"] = max(0.0, float(data["speed_min_upload_mbps"]))
            except (ValueError, TypeError):
                pass
        if "speed_librespeed_server_id" in data:
            cfg["speed"]["librespeed_server_id"] = str(data.get("speed_librespeed_server_id") or "").strip()
        if "speed_ookla_server_id" in data:
            cfg["speed"]["ookla_server_id"] = str(data.get("speed_ookla_server_id") or "").strip()
        if "speed_local_librespeed_port" in data and data["speed_local_librespeed_port"]:
            try:
                cfg["speed"]["local_librespeed_port"] = max(1, min(65535, int(data["speed_local_librespeed_port"])))
            except (ValueError, TypeError):
                pass
        if "speed_history_page_size" in data and data["speed_history_page_size"]:
            try:
                cfg["speed"]["history_page_size"] = max(5, min(100, int(data["speed_history_page_size"])))
            except (ValueError, TypeError):
                pass
        if "speed_iperf_port" in data and data["speed_iperf_port"]:
            try:
                cfg["speed"]["iperf_port"] = max(1, min(65535, int(data["speed_iperf_port"])))
            except (ValueError, TypeError):
                pass

        cfg.setdefault("public", {})
        if "public_dashboard" in data:
            cfg["public"]["dashboard"] = _bool(data.get("public_dashboard"))
        if "public_alerts" in data:
            cfg["public"]["alerts"] = _bool(data.get("public_alerts"))

        cfg.setdefault("web", {})
        if "web_host" in data and data["web_host"]:
            cfg["web"]["host"] = data["web_host"]
        if "web_port" in data and data["web_port"]:
            try:
                cfg["web"]["port"] = int(data["web_port"])
            except (ValueError, TypeError):
                pass
        if "web_trusted_proxies" in data:
            cfg["web"]["trusted_proxies"] = str(data.get("web_trusted_proxies") or "").strip()

        cfg.setdefault("auth", {})
        auth = cfg["auth"]
        auth["enabled"] = _bool(data.get("auth_enabled")) if "auth_enabled" in data else bool(auth.get("enabled", False))
        if "auth_mode" in data and str(data.get("auth_mode") or "").strip() in ("admin_only", "full_app"):
            auth["mode"] = str(data.get("auth_mode") or "admin_only").strip()
        if "auth_username" in data:
            auth["username"] = str(data.get("auth_username") or "admin").strip() or "admin"
        if "auth_idle_timeout_min" in data and str(data.get("auth_idle_timeout_min") or "") != "":
            try:
                auth["idle_timeout_min"] = max(0, min(1440, int(float(data["auth_idle_timeout_min"]))))
            except (ValueError, TypeError):
                pass
        if "auth_max_fails" in data and str(data.get("auth_max_fails") or "") != "":
            try:
                auth["max_fails"] = max(2, min(20, int(float(data["auth_max_fails"]))))
            except (ValueError, TypeError):
                pass
        if "auth_cooldown_sec" in data and str(data.get("auth_cooldown_sec") or "") != "":
            try:
                auth["cooldown_sec"] = max(10, min(3600, int(float(data["auth_cooldown_sec"]))))
            except (ValueError, TypeError):
                pass

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
            current_app.secret_key = auth.get("session_secret") or current_app.secret_key

        try:
            path = save_config(cfg)
            msg = f"Saved to {path}."
        except PermissionError as e:
            return jsonify({"ok": False, "message": f"Could not save: {e}. Run install.sh or chmod config file."}), 500
        return jsonify({"ok": True, "message": msg, "saved_at": _config_saved_at(path)})

    cfg = load_config()
    return render_template("settings.html", cfg=cfg, cfg_defaults=DEFAULT_CONFIG, settings_saved_at=_config_saved_at())


@settings_bp.route("/api/config", methods=["GET"])
def api_config_get():
    cfg = load_config()
    cfg = json.loads(json.dumps(cfg))
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


@settings_bp.route("/api/test-telegram", methods=["POST"])
def api_test_telegram():
    cfg = load_config()
    tg = cfg.get("telegram", {})
    body = request.get_json(silent=True) or {}
    token = str(body.get("bot_token") or body.get("telegram_bot_token") or tg.get("bot_token", "")).strip()
    chat = str(body.get("chat_id") or body.get("telegram_chat_id") or tg.get("chat_id", "")).strip()
    if not token or not chat:
        return jsonify({"ok": False, "error": "bot_token or chat_id not set"}), 400
    if _looks_masked_secret(token):
        err = "saved Telegram token is masked/placeholder, not a real bot token — paste the full token and save again"
        get_storage().log_notification("telegram", False, err)
        return jsonify({"ok": False, "error": err}), 400
    msg = body.get("message") or build_channel_test_message("telegram")
    ok, err = send_telegram(token, chat, msg)
    get_storage().log_notification("telegram", ok, err)
    return jsonify({"ok": ok, "error": err, "message": msg})


@settings_bp.route("/api/test-discord", methods=["POST"])
def api_test_discord():
    cfg = load_config()
    dc = cfg.get("discord", {})
    body = request.get_json(silent=True) or {}
    url = str(body.get("webhook_url") or body.get("discord_webhook_url") or dc.get("webhook_url", "")).strip()
    if not url:
        return jsonify({"ok": False, "error": "webhook_url not set"}), 400
    msg = body.get("message") or build_channel_test_message("discord")
    ok, err = send_discord(url, msg)
    get_storage().log_notification("discord", ok, err)
    return jsonify({"ok": ok, "error": err, "message": msg})


@settings_bp.route("/api/settings/export")
def api_settings_export():
    cfg = load_config()
    body = json.dumps(cfg, indent=2, sort_keys=True).encode()
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return Response(
        body,
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="systor-settings-{ts}.json"'}
    )


@settings_bp.route("/api/settings/import", methods=["POST"])
def api_settings_import():
    up = request.files.get("file")
    if not up or not up.filename:
        return jsonify({"ok": False, "message": "Choose a settings file first."}), 400
    raw = up.read().decode("utf-8", errors="replace")
    try:
        if up.filename.lower().endswith(".json"):
            incoming = json.loads(raw)
        else:
            incoming = _yaml_to_dict(raw)
    except Exception as e:
        return jsonify({"ok": False, "message": f"Could not parse settings file: {e}"}), 400
    if not isinstance(incoming, dict):
        return jsonify({"ok": False, "message": "Imported settings must be a JSON/YAML object."}), 400

    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    def _deep_merge_local(base: dict, overlay: dict) -> dict:
        for k, v in (overlay or {}).items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k] = _deep_merge_local(base[k], v)
            else:
                base[k] = v
        return base

    cfg = _deep_merge_local(cfg, incoming)
    auth = cfg.get("auth") or {}
    if auth.get("enabled") and not auth.get("session_secret"):
        auth["session_secret"] = secrets.token_hex(32)
        cfg["auth"] = auth
    try:
        path = save_config(cfg)
    except PermissionError as e:
        return jsonify({"ok": False, "message": f"Could not save imported settings: {e}"}), 500
    return jsonify({
        "ok": True,
        "message": f"Imported settings from {up.filename} into {path}.",
        "saved_at": _config_saved_at(path)
    })


@settings_bp.route("/api/apply", methods=["POST"])
def api_apply():
    try:
        cfg = load_config()
        path = save_config(cfg)
        os.utime(path, None)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    pid = find_collector_pid()
    sig_sent = False
    if pid:
        try:
            sig = getattr(signal, "SIGHUP", signal.SIGTERM)
            os.kill(pid, sig)
            sig_sent = True
        except (ProcessLookupError, PermissionError):
            pass

    return jsonify({
        "ok": True,
        "message": "Config saved. Collector will pick up changes on its next loop iteration (within "
                   + str(cfg["collector"]["poll_interval_sec"]) + "s)."
                   + (" (SIGHUP sent)" if sig_sent else ""),
        "config_path": str(path),
    })


@settings_bp.route("/api/restart-collector", methods=["POST"])
def api_restart_collector():
    pid = find_collector_pid()
    started_fallback = False
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pid = None

    for _ in range(6):
        time.sleep(0.3)
        cur = find_collector_pid()
        if cur is None:
            break

    # If running under systemd, systemd will restart it if enabled
    is_systemd = bool(os.environ.get("INVOCATION_ID")) or Path("/run/systemd/system").exists()
    if is_systemd:
        try:
            subprocess.run(["systemctl", "restart", "systor-collector"], capture_output=True, timeout=5)
            return jsonify({"ok": True, "message": "systemd unit systor-collector restart invoked."})
        except Exception:
            pass

    cur = find_collector_pid()
    if cur is None:
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

    return jsonify({
        "ok": True,
        "message": ("Collector restart triggered for PID " + str(pid) if pid is not None else "Collector started.")
                   + (" Fallback start used." if started_fallback else "")
    })


@settings_bp.route("/api/restart-web", methods=["POST"])
def api_restart_web():
    runtime = get_runtime_status()
    current_pid = runtime.get("web", {}).get("pid")
    host = runtime.get("web", {}).get("host", "0.0.0.0")
    port = runtime.get("web", {}).get("port", 6677)
    workdir = str(Path(__file__).resolve().parents[2])

    if not current_pid:
        return jsonify({"ok": False, "error": "web process not found"}), 404

    is_systemd = bool(os.environ.get("INVOCATION_ID")) or Path("/run/systemd/system").exists()
    if is_systemd:
        try:
            res = subprocess.run(["systemctl", "restart", "systor-web"], capture_output=True, timeout=5)
            if res.returncode == 0:
                return jsonify({"ok": True, "message": "systemd unit systor-web restarted successfully."})
        except Exception:
            pass

    script = (
        f"cd {workdir} && "
        f"sleep 1 && "
        f"kill -9 {current_pid} >/dev/null 2>&1 || true && "
        f"nohup {sys.executable} -m systor serve web >/dev/null 2>&1 &"
    )
    try:
        subprocess.Popen(["bash", "-lc", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)
        return jsonify({"ok": True, "message": f"Web restart scheduled for PID {current_pid} on {host}:{port}. Refresh in 2-3 seconds."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@settings_bp.route("/api/restart-all", methods=["POST"])
def api_restart_all():
    collector = api_restart_collector().get_json()
    web = api_restart_web().get_json()
    ok = bool(collector.get("ok")) and bool(web.get("ok"))
    code = 200 if ok else 500
    return jsonify({"ok": ok, "collector": collector, "web": web}), code


@settings_bp.route("/logs/raw")
@settings_bp.route("/api/logs")
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


@settings_bp.route("/api/logs/clear", methods=["POST"])
def api_logs_clear():
    cfg = load_config()
    log_file = cfg.get("logging", {}).get("file", "/var/log/systor/systor.log")
    try:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        Path(log_file).write_text("")
        return jsonify({"ok": True, "message": f"Cleared {log_file}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@settings_bp.route("/logs/download")
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
