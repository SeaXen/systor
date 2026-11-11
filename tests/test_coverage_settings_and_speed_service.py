import time
from unittest.mock import patch, MagicMock

from systor.bot import handle_bot_command
from systor.services.speed_service import (
    speed_live_finalize, speed_live_worker, stop_speed_live,
    speed_live_copy, speed_live_update, _speed_live_lock
)
from systor.storage import Storage
from systor.web import create_app


def test_bot_command_handlers(tmp_path):
    """Test all interactive Telegram bot commands."""
    db_file = tmp_path / "bot_test.db"
    store = Storage(str(db_file))

    # /help
    help_resp = handle_bot_command("/help", store)
    assert "Available commands" in help_resp

    # /status
    with patch("systor.bot.collect_snapshot") as mock_snap:
        mock_snap.return_value = {
            "hostname": "bot-node",
            "uptime_sec": 7200,
            "cpu": {"percent": 15.0, "load_1m": 0.5, "load_5m": 0.4, "temp_c": 42.0},
            "memory": {"used_mb": 2048, "total_mb": 8192},
            "disks": [{"mount": "/", "used_pct": 45.0, "used_gb": 50, "size_gb": 100}],
        }
        status_resp = handle_bot_command("/status", store)
        assert "bot-node" in status_resp
        assert "15.0%" in status_resp

    # /alerts (empty)
    alerts_empty = handle_bot_command("/alerts", store)
    assert "System Healthy" in alerts_empty

    # /alerts (with alert)
    store.log_alert("cpu_load", "alert", 6.5, 4.0, "Load high")
    alerts_resp = handle_bot_command("/alerts", store)
    assert "Recent Alerts" in alerts_resp
    assert "cpu_load" in alerts_resp

    # /speed (mocked provider)
    with patch("systor.speed.run_provider") as mock_run:
        mock_run.return_value = {
            "ok": True, "download_mbps": 120.5, "upload_mbps": 45.0, "ping_ms": 12.3
        }
        speed_resp = handle_bot_command("/speed", store)
        assert "Speedtest Complete" in speed_resp
        assert "120.5 Mbps" in speed_resp

    # Unknown
    unk_resp = handle_bot_command("/foobar", store)
    assert "Unknown command" in unk_resp


def test_speed_service_lifecycle():
    """Test live speed service finalize, worker, and stop."""
    # speed_live_finalize success
    called = []
    def on_success(row):
        called.append(row)

    speed_live_update(cancel_requested=False, target="server 1", server_id="1", started_ts=int(time.time()))
    speed_live_finalize(returncode=0, on_success_callback=on_success)
    snap = speed_live_copy()
    assert snap["ok"] is True
    assert snap["phase"] == "complete"
    assert len(called) == 1

    # speed_live_finalize cancelled
    speed_live_update(cancel_requested=True)
    speed_live_finalize(returncode=1)
    snap_c = speed_live_copy()
    assert snap_c["ok"] is False
    assert snap_c["phase"] == "stopped"

    # speed_live_worker when speedtest binary missing
    with patch("shutil.which", return_value=None):
        speed_live_worker("1234")
        snap_m = speed_live_copy()
        assert snap_m["ok"] is False
        assert "not installed" in snap_m["status"]

    # stop_speed_live
    mock_p = MagicMock()
    with _speed_live_lock:
        import systor.services.speed_service as ss
        ss._speed_live_proc = mock_p
    stop_speed_live()
    mock_p.terminate.assert_called_once()


def test_settings_blueprint_endpoints(tmp_path, monkeypatch):
    """Test telegram/discord test endpoints, restart-web, restart-all, and log endpoints."""
    app = create_app()
    app.config["TESTING"] = True

    # Setup temp log file
    log_file = tmp_path / "systor.log"
    log_file.write_text("INFO: starting\nWARNING: high load\nERROR: database locked\nTraceback (most recent call last):\nException: boom\n")

    monkeypatch.setattr("systor.blueprints.settings.load_config", lambda: {
        "logging": {"file": str(log_file)},
        "telegram": {"bot_token": "123:***masked***", "chat_id": "12345"},
        "discord": {"webhook_url": ""},
        "web": {"trusted_proxies": "127.0.0.1"},
        "auth": {"enabled": False},
    })

    with app.test_client() as client:
        # POST /api/test-telegram with masked token
        resp = client.post("/api/test-telegram", json={}, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 400
        assert "masked" in resp.get_json()["error"]

        # POST /api/test-discord with empty url
        resp = client.post("/api/test-discord", json={"webhook_url": ""}, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 400
        assert "not set" in resp.get_json()["error"]

        # POST /api/test-discord with mocked send_discord
        with patch("systor.blueprints.settings.send_discord", return_value=(True, None)):
            resp = client.post("/api/test-discord", json={"webhook_url": "https://discord.com/api/webhooks/123/abc"}, headers={"X-Forwarded-For": "127.0.0.1"})
            assert resp.status_code == 200
            assert resp.get_json()["ok"] is True

        # GET /api/logs with level=errors and level=warnings
        resp = client.get("/api/logs?level=errors", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200
        assert len(resp.get_json()["lines"]) >= 1

        resp = client.get("/api/logs?level=warnings", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200
        assert len(resp.get_json()["lines"]) >= 2

        # GET /logs/download
        resp = client.get("/logs/download", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200
        assert b"starting" in resp.data

        # POST /api/logs/clear
        resp = client.post("/api/logs/clear", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200
        assert log_file.read_text() == ""

        # POST /api/restart-web
        with patch("systor.blueprints.settings.get_runtime_status") as mock_r:
            mock_r.return_value = {"web": {"pid": 9999, "host": "127.0.0.1", "port": 6677}}
            with patch("subprocess.Popen"):
                # Reset rate limit timer
                import systor.blueprints.settings as s_bp
                s_bp._LAST_WEB_RESTART = 0.0
                resp = client.post("/api/restart-web", headers={"X-Forwarded-For": "127.0.0.1"})
                assert resp.status_code == 200
                assert resp.get_json()["ok"] is True

        # POST /api/backup/export
        resp = client.get("/api/backup/export?include_secrets=1", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200
        assert resp.headers.get("Content-Type") == "application/gzip"
