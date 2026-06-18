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
from .metrics import collect_snapshot, read_top_processes, read_total_memory_mb
from .notifier import Notifier, send_telegram, send_discord
from .storage import Storage, DEFAULT_DB_PATH

log = logging.getLogger("systor.web")
_running = True


def _handle_term(signum, _frame):
    global _running
    log.info("web: received signal %d, shutting down", signum)
    _running = False


signal.signal(signal.SIGTERM, _handle_term)
signal.signal(signal.SIGINT, _handle_term)


# Cached storage (lazy-init so we don't block on slow disks)
_storage: Storage | None = None
_storage_lock = threading.Lock()


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
        hours = int(request.args.get("hours", 6))
        data = get_storage().series(metric, hours=hours)
        # downsample to <= 600 points for charts
        if len(data) > 600:
            step = len(data) // 600 + 1
            data = data[::step]
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
        return render_template("dashboard.html")

    @app.route("/alerts")
    def page_alerts():
        return render_template("alerts.html")

    @app.route("/logs")
    def page_logs():
        cfg = load_config()
        return render_template("logs.html", log_path=cfg.get("logging", {}).get("file", "/var/log/systor/systor.log"))

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
                cfg["telegram"]["bot_token"] = str(data["telegram_bot_token"]).strip()
            if "telegram_chat_id" in data:
                cfg["telegram"]["chat_id"] = str(data["telegram_chat_id"] or "").strip()
            # Update discord
            if "discord_enabled" in data:
                cfg["discord"]["enabled"] = _bool(data.get("discord_enabled"))
            if "discord_webhook_url" in data and data["discord_webhook_url"]:
                cfg["discord"]["webhook_url"] = str(data["discord_webhook_url"]).strip()
            # Update poll interval
            if "poll_interval_sec" in data and data["poll_interval_sec"]:
                try: cfg["collector"]["poll_interval_sec"] = max(5, int(data["poll_interval_sec"]))
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
        msg = body.get("message", "🧪 systor test alert from web UI")
        ok, err = send_telegram(token, chat, msg)
        get_storage().log_notification("telegram", ok, err)
        return jsonify({"ok": ok, "error": err})

    @app.route("/api/test-discord", methods=["POST"])
    def api_test_discord():
        cfg = load_config()
        dc = cfg.get("discord", {})
        body = request.get_json(silent=True) or {}
        # Allow Settings page to test currently typed values before saving.
        url = str(body.get("webhook_url") or body.get("discord_webhook_url") or dc.get("webhook_url", "")).strip()
        if not url:
            return jsonify({"ok": False, "error": "webhook_url not set"}), 400
        msg = body.get("message", "🧪 systor test alert from web UI")
        ok, err = send_discord(url, msg)
        get_storage().log_notification("discord", ok, err)
        return jsonify({"ok": ok, "error": err})

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

    @app.route("/logs/raw")
    def api_logs_raw():
        cfg = load_config()
        log_file = cfg.get("logging", {}).get("file", "/var/log/systor/systor.log")
        lines = int(request.args.get("lines", 200))
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
