import json
import io
from unittest.mock import patch, MagicMock
from systor.web import create_app
from systor.config import save_config, load_config
from systor.services.backup_service import create_backup_archive


def _setup_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))
    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_file])

    cfg = load_config()
    cfg["auth"] = {
        "enabled": False,
        "username": "admin",
        "password_hash": "",
    }
    save_config(cfg)

    app = create_app(start_worker=False)
    app.config["TESTING"] = True
    return app


def test_settings_full_form_submit(tmp_path, monkeypatch):
    """Test full settings form POST updating every configuration sector."""
    app = _setup_app(tmp_path, monkeypatch)
    client = app.test_client()

    form_data = {
        "threshold_cpu_load_1m_enabled": "true",
        "threshold_cpu_load_1m_value": "4.0",
        "threshold_cpu_load_1m_duration": "2",
        "threshold_cpu_temp_c_enabled": "true",
        "threshold_cpu_temp_c_value": "80",
        "threshold_cpu_temp_c_duration": "1",
        "threshold_mem_free_mb_enabled": "true",
        "threshold_mem_free_mb_value": "1000",
        "threshold_mem_free_mb_duration": "2",
        "threshold_swap_used_mb_enabled": "true",
        "threshold_swap_used_mb_value": "500",
        "threshold_swap_used_mb_duration": "3",
        "threshold_disk_used_pct_enabled": "true",
        "threshold_disk_used_pct_value": "90",
        "threshold_disk_used_pct_duration": "2",
        "cooldown_sec": "300",
        "telegram_enabled": "true",
        "telegram_bot_token": "123:ABC",
        "telegram_chat_id": "456",
        "discord_enabled": "true",
        "discord_webhook_url": "https://discord.com/api/webhooks/test",
        "poll_interval_sec": "3",
        "network_default_hours": "12",
        "network_auto_refresh_sec": "10",
        "network_default_granularity": "day",
        "network_default_bar_days": "7",
        "network_default_table_days": "14",
        "network_default_iface": "eth0",
        "network_hide_virtual_default": "true",
        "dashboard_default_hours": "8",
        "dashboard_refresh_sec": "4",
        "dashboard_chart_refresh_sec": "6",
        "apps_auto_refresh_sec": "5",
        "apps_default_limit": "15",
        "apps_default_scope": "host",
        "apps_default_sort": "cpu",
        "speed_page_refresh_sec": "30",
        "speed_default_provider": "cloudflare",
        "speed_auto_enabled": "true",
        "speed_auto_provider": "ookla",
        "speed_auto_interval_min": "60",
        "speed_notify_enabled": "true",
        "speed_min_download_mbps": "50.0",
        "speed_min_upload_mbps": "15.0",
        "speed_librespeed_server_id": "1",
        "speed_ookla_server_id": "2",
        "speed_local_librespeed_port": "8989",
        "speed_history_page_size": "50",
        "speed_iperf_port": "5201",
        "public_dashboard": "true",
        "public_alerts": "true",
        "web_host": "0.0.0.0",
        "web_port": "6677",
        "web_trusted_proxies": "127.0.0.1",
        "auth_enabled": "false",
        "auth_username": "admin",
    }

    res = client.post("/settings", data=form_data, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res.status_code == 200
    assert res.get_json()["ok"] is True

    # Test /api/restart-all with mocked components
    with patch("systor.blueprints.settings.api_restart_collector") as mock_col:
        with patch("systor.blueprints.settings.api_restart_web") as mock_web:
            mock_col.return_value.get_json.return_value = {"ok": True}
            mock_web.return_value.get_json.return_value = {"ok": True}
            res_rst_all = client.post("/api/restart-all", environ_base={"REMOTE_ADDR": "127.0.0.1"})
            assert res_rst_all.status_code == 200
            assert res_rst_all.get_json()["ok"] is True
    """Test /api/config, /settings POST, and /api/apply routes."""
    app = _setup_app(tmp_path, monkeypatch)
    client = app.test_client()

    # GET /api/config
    res = client.get("/api/config", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert "config" in data

    # POST /settings valid update
    res_post = client.post(
        "/settings",
        data={"poll_interval_sec": "7", "telegram_enabled": "false"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert res_post.status_code == 200
    assert res_post.get_json()["ok"] is True

    # Check updated value in config
    cfg = load_config()
    assert cfg["collector"]["poll_interval_sec"] == 7

    # POST /api/apply applies changes to collector
    res_apply = client.post("/api/apply", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_apply.status_code == 200
    assert res_apply.get_json()["ok"] is True


def test_settings_pages_and_runtime(tmp_path, monkeypatch):
    """Test /settings, /alerts, /logs HTML pages and telemetry APIs."""
    app = _setup_app(tmp_path, monkeypatch)
    client = app.test_client()

    # Page renders
    for path in ("/settings", "/alerts", "/logs"):
        res = client.get(path, environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert res.status_code == 200

    # API endpoints
    res_alerts = client.get("/api/alerts", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_alerts.status_code == 200
    assert isinstance(res_alerts.get_json(), list)

    res_notifs = client.get("/api/notifications", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_notifs.status_code == 200
    assert isinstance(res_notifs.get_json(), list)

    res_runtime = client.get("/api/runtime", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_runtime.status_code == 200
    assert res_runtime.get_json()["ok"] is True


def test_settings_test_notifications(tmp_path, monkeypatch):
    """Test notification dispatch to Telegram and Discord."""
    app = _setup_app(tmp_path, monkeypatch)
    client = app.test_client()

    with patch("systor.notifier.safe_urlopen") as mock_url:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.getcode.return_value = 200
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__.return_value = mock_resp
        mock_url.return_value = mock_resp

        # Telegram test
        res_tg = client.post(
            "/api/test-telegram",
            json={"bot_token": "123:ABC", "chat_id": "123"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        assert res_tg.status_code == 200
        assert res_tg.get_json()["ok"] is True

        # Discord test
        res_dc = client.post(
            "/api/test-discord",
            json={"webhook_url": "https://discord.com/api/webhooks/test"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        assert res_dc.status_code == 200
        assert res_dc.get_json()["ok"] is True


def test_settings_export_and_import(tmp_path, monkeypatch):
    """Test /api/settings/export and /api/settings/import."""
    app = _setup_app(tmp_path, monkeypatch)
    client = app.test_client()

    # Export
    res_exp = client.get("/api/settings/export", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_exp.status_code == 200
    exported_data = res_exp.get_json()
    assert "collector" in exported_data

    # Modify and import
    exported_data["collector"] = {"poll_interval_sec": 9}
    file_bytes = io.BytesIO(json.dumps(exported_data).encode("utf-8"))
    res_imp = client.post(
        "/api/settings/import",
        data={"file": (file_bytes, "settings.json")},
        content_type="multipart/form-data",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert res_imp.status_code == 200
    assert res_imp.get_json()["ok"] is True


def test_settings_logs_and_backup_endpoints(tmp_path, monkeypatch):
    """Test logs viewing, download, clearing and backup export/restore endpoints."""
    app = _setup_app(tmp_path, monkeypatch)
    client = app.test_client()

    # Create dummy storage and logs
    with app.app_context():
        from systor.blueprints.common import get_storage
        store = get_storage()
        store.log_alert("test.cpu", "alert", 90, 80, "CPU alert")

    # /api/logs and /logs/raw
    res_logs = client.get("/api/logs", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_logs.status_code == 200
    assert "lines" in res_logs.get_json()

    res_raw = client.get("/logs/raw", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_raw.status_code == 200

    # /logs/download
    res_dl = client.get("/logs/download", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_dl.status_code == 200

    # /api/logs/clear
    res_clr = client.post("/api/logs/clear", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_clr.status_code == 200

    # /api/backup/export
    res_bk_exp = client.get("/api/backup/export", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_bk_exp.status_code == 200
    assert "application/gzip" in res_bk_exp.headers.get("Content-Type", "")

    # /api/backup/restore
    archive_bytes, _ = create_backup_archive(store, include_secrets=False)
    data = {
        "backup_file": (io.BytesIO(archive_bytes), "backup.tar.gz"),
        "restore_secrets": "0",
    }
    res_restore = client.post(
        "/api/backup/restore",
        data=data,
        content_type="multipart/form-data",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert res_restore.status_code == 200
    assert res_restore.get_json()["ok"] is True
