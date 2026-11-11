import pytest
from systor.web import create_app
from systor.config import save_config, load_config


def test_S02_first_run_requires_setup(tmp_path, monkeypatch):
    """S02: Fresh app requested from LAN (192.168.1.50) redirects sensitive routes to /setup."""
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_path])

    app = create_app(start_worker=False)
    app.config["TESTING"] = True
    client = app.test_client()

    # Request from LAN IP 192.168.1.50 to /settings
    res = client.get("/settings", environ_base={"REMOTE_ADDR": "192.168.1.50"})
    assert res.status_code == 302
    assert "/setup" in res.headers["Location"]

    # Dashboard / and /api/snapshot remain visible
    res_dash = client.get("/", environ_base={"REMOTE_ADDR": "192.168.1.50"})
    assert res_dash.status_code == 200

    res_snap = client.get("/api/snapshot", environ_base={"REMOTE_ADDR": "192.168.1.50"})
    assert res_snap.status_code == 200


def test_S02_mutations_require_admin_on_lan(tmp_path, monkeypatch):
    """S02: Mutating API routes from LAN return 401/403 without setup/login."""
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_path])

    app = create_app(start_worker=False)
    app.config["TESTING"] = True
    client = app.test_client()

    # Attempting to save settings without setup returns 403
    res = client.post(
        "/api/settings/save",
        environ_base={"REMOTE_ADDR": "192.168.1.50"},
        json={"collector": {"poll_interval_sec": 10}}
    )
    assert res.status_code == 403
    data = res.get_json()
    assert data["ok"] is False
    assert "setup required" in data["error"]

    # Perform setup with valid password
    setup_res = client.post(
        "/api/setup",
        environ_base={"REMOTE_ADDR": "192.168.1.50"},
        json={
            "username": "admin",
            "password": "StrongPassword123!",
            "confirm_password": "StrongPassword123!"
        }
    )
    assert setup_res.status_code == 200
    assert setup_res.get_json()["ok"] is True

    # After setup, the session is authenticated and can access settings
    res_after = client.get("/settings", environ_base={"REMOTE_ADDR": "192.168.1.50"})
    assert res_after.status_code == 200
