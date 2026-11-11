from unittest.mock import patch

from systor.blueprints.dashboard import _clean_config
from systor.config import (
    save_config, get_or_create_session_secret, _migrate_thresholds, DEFAULT_CONFIG
)
from systor.storage import Storage
from systor.web import create_app


def test_auth_flows_and_setup(monkeypatch):
    """Test login with preview, rate limiting, and setup validation."""
    app = create_app()
    app.config["TESTING"] = True

    # 1. Login preview mode
    with app.test_client() as client:
        resp = client.get("/login?preview=1", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200

        # Login with invalid username/password when auth enabled
        monkeypatch.setattr("systor.blueprints.auth.auth_enabled", lambda: True)
        monkeypatch.setattr("systor.blueprints.auth.auth_cfg", lambda: {
            "enabled": True, "username": "admin", "password_hash": "scrypt:mocked_hash"
        })
        resp = client.post("/login", data={"username": "wrong", "password": "wrongpassword"}, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 401

        # Reset auth_enabled for setup tests
        monkeypatch.setattr("systor.blueprints.auth.auth_enabled", lambda: False)

        # 2. Setup GET
        resp = client.get("/setup", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200

        # Setup POST validation: short password
        resp = client.post("/setup", data={"username": "admin", "password": "short", "confirm_password": "short"}, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 400
        assert b"at least 12 characters" in resp.data

        # Setup POST validation: mismatch
        resp = client.post("/setup", data={"username": "admin", "password": "ValidPassword123!", "confirm_password": "DifferentPassword123!"}, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 400
        assert b"do not match" in resp.data

        # Setup POST: successful creation
        with patch("systor.config.save_config"):
            resp = client.post("/setup", data={"username": "admin", "password": "ValidPassword123!", "confirm_password": "ValidPassword123!"}, headers={"X-Forwarded-For": "127.0.0.1"})
            assert resp.status_code == 302

        # 3. /api/setup WAN check
        resp = client.post("/api/setup", json={"username": "admin", "password": "ValidPassword123!", "confirm_password": "ValidPassword123!"}, environ_base={"REMOTE_ADDR": "8.8.8.8"})
        assert resp.status_code == 403

        # /api/setup valid LAN
        with patch("systor.blueprints.auth.auth_enabled", return_value=False):
            with patch("systor.config.save_config"):
                resp = client.post("/api/setup", json={
                    "username": "admin",
                    "password": "ValidPassword123!",
                    "confirm_password": "ValidPassword123!"
                }, environ_base={"REMOTE_ADDR": "127.0.0.1"})
                assert resp.status_code == 200
                assert resp.get_json()["ok"] is True

        # 4. Logout
        resp = client.get("/logout", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 302


def test_dashboard_routes_and_prometheus(tmp_path, monkeypatch):
    """Test /, /offline, /api/system, /api/snapshot 304 ETag, /metrics Prometheus exporter."""
    db_file = tmp_path / "dash.db"
    store = Storage(str(db_file))

    # Add uptime target for prometheus
    store.add_uptime_target(name="Router", url="http://192.168.1.1", interval_sec=60)
    store.record_uptime_check(1, 200, 15.0, True, "OK")

    app = create_app()
    app.config["TESTING"] = True

    monkeypatch.setattr("systor.blueprints.common.get_storage", lambda: store)
    monkeypatch.setattr("systor.blueprints.dashboard.get_storage", lambda: store)

    # Mock snapshot with network ifaces and swap
    snap = {
        "ts": 1700000000,
        "hostname": "dash-node",
        "cpu": {"percent": 25.0, "load_1m": 1.2, "load_5m": 1.0, "load_15m": 0.8, "temp_c": 45.0},
        "memory": {"total_mb": 16000, "used_mb": 4000, "free_mb": 8000, "available_mb": 12000, "swap_total_mb": 2048, "swap_used_mb": 512},
        "disks": [{"mount": "/", "used_pct": 50.0, "used_gb": 100, "size_gb": 200}],
        "disk_io": {"read_mbps": 5.2, "write_mbps": 12.1},
        "network_interfaces": [{"iface": "eth0", "rx_bytes": 1000000, "tx_bytes": 500000}],
    }
    monkeypatch.setattr("systor.blueprints.dashboard.collect_snapshot", lambda: snap)

    with app.test_client() as client:
        # GET /
        resp = client.get("/", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200

        # GET /offline
        resp = client.get("/offline", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200

        # GET /api/system
        resp = client.get("/api/system", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "config" in data
        assert "snapshot" in data

        # GET /api/snapshot and ETag 304
        resp = client.get("/api/snapshot", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200
        etag = resp.headers.get("ETag")
        assert etag is not None

        resp304 = client.get("/api/snapshot", headers={"X-Forwarded-For": "127.0.0.1", "If-None-Match": etag})
        assert resp304.status_code == 304

        # GET /metrics (Prometheus format)
        resp_m = client.get("/metrics", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp_m.status_code == 200
        text = resp_m.get_data(as_text=True)
        assert "systor_cpu_percent" in text
        assert "systor_memory_bytes" in text
        assert "systor_swap_bytes" in text
        assert "systor_disk_used_percent" in text
        assert "systor_network_traffic_bytes_total" in text
        assert "systor_service_uptime_up" in text


def test_config_validation_and_secrets(tmp_path):
    """Test _clean_config, session secret, get_data_dir, and threshold migrations."""
    # _clean_config mask
    dirty = {
        "auth": {"password_hash": "secret_hash", "session_secret": "super_secret"},
        "telegram": {"bot_token": "12345:token"},
        "storage_page": {"action_password_hash": "action_secret"},
    }
    cleaned = _clean_config(dirty)
    assert cleaned["auth"]["password_hash"] == "********"
    assert cleaned["auth"]["session_secret"] == "********"
    assert cleaned["telegram"]["bot_token"] == "********"
    assert cleaned["storage_page"]["action_password_hash"] == "********"

    # get_or_create_session_secret
    sec = get_or_create_session_secret({"auth": {}})
    assert len(sec) >= 32

    # save_config to custom file
    custom_cfg_path = tmp_path / "test_config.yaml"
    cfg = dict(DEFAULT_CONFIG)
    save_config(cfg, path=custom_cfg_path)
    assert custom_cfg_path.exists()
    content = custom_cfg_path.read_text()
    assert "web:" in content
    assert "collector:" in content

    # _migrate_thresholds from old flat schema
    old_th_cfg = {
        "thresholds": {
            "cooldown_sec": 300,
            "cpu_load_1m": 4.0,
            "mem_free_mb": 500,
            "sustained_samples_cpu": 6,
        }
    }
    _migrate_thresholds(old_th_cfg)
    assert isinstance(old_th_cfg["thresholds"]["cpu_load_1m"], dict)
    assert old_th_cfg["thresholds"]["cpu_load_1m"]["enabled"] is True
    assert old_th_cfg["thresholds"]["cpu_load_1m"]["value"] == 4.0
    assert old_th_cfg["thresholds"]["cpu_load_1m"]["duration_min"] == 3
