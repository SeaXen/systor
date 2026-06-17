"""systor web dashboard — Flask app on port 6677.

Pages:
  /              — live dashboard (auto-refresh, dark theme)
  /api/snapshot  — current metrics JSON
  /api/series    — historical time-series JSON
  /api/alerts    — recent alerts JSON
  /api/notifications — recent notification log
  /api/system    — full system snapshot
  /settings      — web UI to edit thresholds, telegram, discord
  /logs          — recent log lines (read from log file)
  /health        — simple liveness probe
"""
from __future__ import annotations
import json
import logging
import re
import signal
import sys
import threading
import time
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, render_template, request, abort, Response

from .config import load_config, save_config
from .metrics import collect_snapshot
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

    @app.route("/api/notifications")
    def api_notifications():
        limit = int(request.args.get("limit", 50))
        return jsonify(get_storage().recent_notifications(limit=limit))

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
            # Update thresholds
            for k in ("cpu_load_1m", "cpu_temp_c", "mem_free_mb", "swap_used_mb", "disk_used_pct"):
                v = data.get(f"threshold_{k}")
                if v is not None and v != "":
                    try: cfg["thresholds"][k] = float(v)
                    except ValueError: pass
            for k in ("sustained_samples_cpu", "sustained_samples_temp", "sustained_samples_mem",
                      "sustained_samples_swap", "sustained_samples_disk", "cooldown_sec"):
                v = data.get(k)
                if v is not None and v != "":
                    try: cfg["thresholds"][k] = int(v)
                    except ValueError: pass
            # Update telegram
            if "telegram_enabled" in data:
                cfg["telegram"]["enabled"] = (data.get("telegram_enabled") in ("true", "on", "1", True))
            if "telegram_bot_token" in data and data["telegram_bot_token"]:
                cfg["telegram"]["bot_token"] = data["telegram_bot_token"]
            if "telegram_chat_id" in data:
                cfg["telegram"]["chat_id"] = data["telegram_chat_id"]
            # Update discord
            if "discord_enabled" in data:
                cfg["discord"]["enabled"] = (data.get("discord_enabled") in ("true", "on", "1", True))
            if "discord_webhook_url" in data and data["discord_webhook_url"]:
                cfg["discord"]["webhook_url"] = data["discord_webhook_url"]
            # Update poll interval
            if "poll_interval_sec" in data and data["poll_interval_sec"]:
                try: cfg["collector"]["poll_interval_sec"] = int(data["poll_interval_sec"])
                except ValueError: pass
            try:
                path = save_config(cfg)
                msg = f"Saved to {path}. Restart the collector for changes to take effect."
            except PermissionError as e:
                msg = f"Could not save: {e}. Run install.sh or chmod the config file."
            return jsonify({"ok": True, "message": msg})
        # GET
        cfg = load_config()
        return render_template("settings.html", cfg=cfg)

    @app.route("/api/test-telegram", methods=["POST"])
    def api_test_telegram():
        cfg = load_config()
        tg = cfg.get("telegram", {})
        token = tg.get("bot_token", "")
        chat = tg.get("chat_id", "")
        if not token or not chat:
            return jsonify({"ok": False, "error": "bot_token or chat_id not set"}), 400
        body = request.get_json(silent=True) or {}
        msg = body.get("message", "🧪 systor test alert from web UI")
        ok, err = send_telegram(token, chat, msg)
        get_storage().log_notification("telegram", ok, err)
        return jsonify({"ok": ok, "error": err})

    @app.route("/api/test-discord", methods=["POST"])
    def api_test_discord():
        cfg = load_config()
        dc = cfg.get("discord", {})
        url = dc.get("webhook_url", "")
        if not url:
            return jsonify({"ok": False, "error": "webhook_url not set"}), 400
        body = request.get_json(silent=True) or {}
        msg = body.get("message", "🧪 systor test alert from web UI")
        ok, err = send_discord(url, msg)
        get_storage().log_notification("discord", ok, err)
        return jsonify({"ok": ok, "error": err})

    @app.route("/logs/raw")
    def api_logs_raw():
        cfg = load_config()
        log_file = cfg.get("logging", {}).get("file", "/var/log/systor/systor.log")
        lines = int(request.args.get("lines", 200))
        return jsonify(read_log_tail(log_file, lines))

    @app.route("/health")
    def health():
        return jsonify({"ok": True, "ts": time.time()})

    return app


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
