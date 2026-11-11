from unittest.mock import patch
from systor.web import create_app
from systor.config import save_config, load_config
from systor.storage import Storage
from systor.bot import handle_bot_command


def _setup_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))
    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_file])

    cfg = load_config()
    cfg["auth"] = {"enabled": False}
    save_config(cfg)

    app = create_app(start_worker=False)
    app.config["TESTING"] = True
    return app


def test_speed_blueprint_and_service(tmp_path, monkeypatch):
    """Test /speed UI and /api/speedtests, /api/speed/status, run, log."""
    app = _setup_app(tmp_path, monkeypatch)
    client = app.test_client()

    # /speed page
    res_page = client.get("/speed", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_page.status_code == 200

    # /api/speedtests
    res_list = client.get("/api/speedtests?provider=all&type=all&limit=25", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_list.status_code == 200
    assert "rows" in res_list.get_json()

    # /api/speed/status
    res_stat = client.get("/api/speed/status", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_stat.status_code == 200

    # /api/speed/options
    res_opt = client.get("/api/speed/options", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_opt.status_code == 200

    # /api/speed/run with mock provider
    with patch("systor.speed.run_provider", return_value={"ok": True, "provider": "cloudflare", "dl_mbps": 120.0, "ul_mbps": 40.0, "ping_ms": 15.0}):
        res_run = client.post("/api/speed/run", json={"provider": "cloudflare"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert res_run.status_code == 200
        assert res_run.get_json()["ok"] is True

    # /api/speed/log
    res_log = client.post(
        "/api/speed/log",
        json={"provider": "manual", "dl_mbps": 85.5, "ul_mbps": 30.2, "ping_ms": 12.0},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert res_log.status_code == 200


def test_telegram_bot_commands(tmp_path):
    """Test Telegram bot command generation (/start, /ping, /status, /alerts)."""
    db_file = tmp_path / "bot_test.db"
    store = Storage(str(db_file))

    # /start & /help
    msg_start = handle_bot_command("/start", store)
    assert "Systor Server Bot" in msg_start

    # /ping
    assert "Pong" in handle_bot_command("/ping", store)

    # /status
    msg_status = handle_bot_command("/status", store)
    assert "System Status" in msg_status

    # /alerts (empty)
    msg_alerts = handle_bot_command("/alerts", store)
    assert "Healthy" in msg_alerts

    # /alerts (with recorded alert)
    store.log_alert("cpu_pct", "alert", 92, 80, "High CPU load")
    msg_alerts_active = handle_bot_command("/alerts", store)
    assert "High CPU load" in msg_alerts_active


def test_auth_login_logout_flow(tmp_path, monkeypatch):
    """Test /login, /logout, and /api/setup."""
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))
    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_file])

    app = create_app(start_worker=False)
    app.config["TESTING"] = True
    client = app.test_client()

    # Initial /setup page
    res_setup_page = client.get("/setup", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_setup_page.status_code == 200

    # Execute first-run setup
    res_setup = client.post(
        "/api/setup",
        json={"username": "superadmin", "password": "SecurePassword2026!", "confirm_password": "SecurePassword2026!"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert res_setup.status_code == 200
    assert res_setup.get_json()["ok"] is True

    # Log out to test login form
    client.get("/logout", environ_base={"REMOTE_ADDR": "127.0.0.1"})

    # Login with bad password
    res_bad_pw = client.post(
        "/login",
        data={"username": "superadmin", "password": "wrongpassword"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert res_bad_pw.status_code == 401
    assert "Invalid username or password" in res_bad_pw.get_data(as_text=True)

    # Login with good password
    res_login = client.post(
        "/login",
        data={"username": "superadmin", "password": "SecurePassword2026!"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    # Redirects on success
    assert res_login.status_code == 302

    # Logout
    res_logout = client.get("/logout", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_logout.status_code == 302
