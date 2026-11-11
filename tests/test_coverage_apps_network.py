import time
from unittest.mock import patch, MagicMock
from systor.web import create_app
from systor.config import save_config, load_config
from systor.services.runtime_service import (
    get_runtime_status, set_cached_apps, get_cached_apps
)


def _setup_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))
    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_file])

    cfg = load_config()
    cfg["auth"] = {"enabled": False}
    cfg["terminal"] = {"enabled": True}
    cfg["systemd"] = {"allowed_units": ["systor-*.service", "nginx.service"]}
    save_config(cfg)

    app = create_app(start_worker=False)
    app.config["TESTING"] = True
    return app


def test_apps_endpoints_and_runtime_service(tmp_path, monkeypatch):
    """Test /apps UI and /api/apps, /api/top-processes, and runtime caching."""
    app = _setup_app(tmp_path, monkeypatch)
    client = app.test_client()

    # /apps page
    res_page = client.get("/apps", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_page.status_code == 200

    # /api/top-processes
    res_top = client.get("/api/top-processes", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_top.status_code == 200
    assert "processes" in res_top.get_json()

    # /api/apps query
    res_apps = client.get("/api/apps?scope=all&sort=cpu&limit=20&page=1", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_apps.status_code == 200
    assert res_apps.get_json()["ok"] is True

    # Test cache setters and getters
    sample_apps = [{"name": "mock-app", "cpu_pct": 1.2, "mem_mb": 50}]
    set_cached_apps(time.time(), 20, sample_apps, [])
    cached = get_cached_apps()
    assert cached is not None

    # Runtime status
    status = get_runtime_status()
    assert "web" in status


def test_apps_systemd_and_compose(tmp_path, monkeypatch):
    """Test systemd restart, systemd audit log, and docker compose inspection."""
    app = _setup_app(tmp_path, monkeypatch)
    client = app.test_client()

    # Systemd list
    res_sys = client.get("/api/apps/systemd", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_sys.status_code == 200
    assert "services" in res_sys.get_json()

    # Systemd restart (allow-listed)
    with patch("shutil.which", return_value="/usr/bin/systemctl"):
        with patch("systor.services.systemd_service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            res_rst = client.post("/api/apps/systemd/nginx.service/restart", environ_base={"REMOTE_ADDR": "127.0.0.1"})
            assert res_rst.status_code == 200
            assert res_rst.get_json()["ok"] is True

    # Systemd audit
    res_audit = client.get("/api/apps/systemd/audit", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_audit.status_code == 200
    assert "actions" in res_audit.get_json()

    # Compose inspection
    with patch("subprocess.run") as mock_sp:
        mock_sp.return_value = MagicMock(returncode=0, stdout='[{"Name":"myproject","Status":"running"}]')
        res_cmp = client.get("/api/apps/compose", environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert res_cmp.status_code == 200


def test_network_endpoints_and_uptime(tmp_path, monkeypatch):
    """Test /network UI, /api/network-series, usage, and uptime target management."""
    app = _setup_app(tmp_path, monkeypatch)
    client = app.test_client()

    # /network page
    res_page = client.get("/network", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_page.status_code == 200

    # Network series & usage
    res_ser = client.get("/api/network-series?hours=6", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_ser.status_code == 200

    res_use = client.get("/api/network-usage", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_use.status_code == 200

    res_if = client.get("/api/network-interfaces", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_if.status_code == 200

    # Add uptime target
    res_add = client.post(
        "/api/uptime/targets",
        json={"name": "Test Site", "url": "https://example.com", "method": "GET", "expected_status": 200, "interval_sec": 30},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert res_add.status_code == 200
    target_id = res_add.get_json().get("id")
    assert target_id is not None

    # Get uptime list
    res_up = client.get("/api/uptime", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_up.status_code == 200
    assert len(res_up.get_json().get("targets", [])) >= 1

    # Update uptime target
    res_edit = client.put(
        f"/api/uptime/targets/{target_id}",
        json={"name": "Test Site Renamed", "url": "https://example.com/api", "interval_sec": 60, "enabled": True},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert res_edit.status_code == 200

    # Check now
    with patch("systor.services.uptime_service.ping_target", return_value=(True, 200, 45.2, None)):
        res_now = client.post("/api/uptime/check-now", json={"id": target_id}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert res_now.status_code == 200
        assert res_now.get_json()["ok"] is True

    # Delete uptime target
    res_del = client.delete(f"/api/uptime/targets/{target_id}", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_del.status_code == 200

    # SSL & Security audit
    res_ssl = client.get("/api/ssl/certs", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_ssl.status_code == 200

    res_sec = client.get("/api/network/security", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res_sec.status_code == 200
