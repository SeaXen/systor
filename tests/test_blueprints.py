"""Tests for Flask modular blueprints: route registration, status codes, and backwards compatibility."""
import pytest
from pathlib import Path

from systor.web import create_app
from systor.storage import Storage
from systor.blueprints import common


@pytest.fixture
def test_client(monkeypatch, tmp_path):
    # Configure isolated database
    db_file = tmp_path / "test_web.db"
    store = Storage(db_path=db_file)
    common._storage = store
    monkeypatch.setattr("systor.blueprints.common.get_storage", lambda: store)

    cfg = {
        "web": {"host": "127.0.0.1", "port": 6677, "trusted_proxies": "127.0.0.1, ::1"},
        "collector": {"retention_days": 7, "rollup_retention_days": 30},
        "auth": {"enabled": False, "mode": "admin_only"},
        "public": {
            "enabled": True,
            "show_cpu": True,
            "show_memory": True,
            "show_disks": True,
            "show_speed": True,
            "show_apps": True,
            "show_network": True,
            "show_storage": True,
        },
        "storage_page": {"allowed_roots": [str(tmp_path)]},
    }
    monkeypatch.setattr("systor.blueprints.common.load_config", lambda: cfg)
    monkeypatch.setattr("systor.config.load_config", lambda: cfg)

    # Disable apps background cache thread in test runner
    monkeypatch.setattr("systor.web.start_apps_cache_worker", lambda: None)

    app = create_app(start_worker=False)
    app.config["TESTING"] = True
    return app.test_client()


def test_health_endpoint(test_client):
    res = test_client.get("/health")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert "ts" in data


def test_version_endpoint(test_client):
    res = test_client.get("/api/version")
    assert res.status_code == 200
    data = res.get_json()
    assert data["name"] == "systor"
    assert "version" in data


def test_access_endpoint(test_client):
    res = test_client.get("/api/access", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert "port" in data
    assert "bind_host" in data


def test_snapshot_endpoint(test_client):
    res = test_client.get("/api/snapshot")
    assert res.status_code == 200
    data = res.get_json()
    assert "hostname" in data
    assert "cpu" in data
    assert "memory" in data
    assert "disks" in data


def test_series_endpoint(test_client):
    res = test_client.get("/api/series?metric=load_1m&hours=1")
    assert res.status_code == 200
    data = res.get_json()
    assert "data" in data
    assert data["metric"] == "load_1m"


def test_apps_endpoint(test_client, monkeypatch):
    monkeypatch.setattr(
        "systor.blueprints.apps.get_cached_apps",
        lambda: {"ts": 0, "limit": 0, "host_rows": [], "docker_rows": []}
    )
    monkeypatch.setattr("systor.blueprints.apps.get_host_apps", lambda limit=96: [])
    monkeypatch.setattr("systor.blueprints.apps.get_docker_apps", lambda limit=96: [])
    res = test_client.get("/api/apps")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert "rows" in data
    assert "summary" in data


def test_top_processes_endpoint(test_client, monkeypatch):
    monkeypatch.setattr(
        "systor.blueprints.apps.read_top_processes",
        lambda n=10, by="cpu": [{"pid": 1234, "name": "python", "cpu_percent": 5.2, "mem_mb": 150}]
    )
    res = test_client.get("/api/top-processes")
    assert res.status_code == 200
    data = res.get_json()
    assert "processes" in data
    assert len(data["processes"]) == 1
    assert data["processes"][0]["pid"] == 1234


def test_network_endpoints(test_client):
    res_series = test_client.get("/api/network-series?hours=1")
    assert res_series.status_code == 200
    assert "data" in res_series.get_json()

    res_usage = test_client.get("/api/network-usage?granularity=day")
    assert res_usage.status_code == 200
    assert "data" in res_usage.get_json()

    res_ifaces = test_client.get("/api/network-interfaces")
    assert res_ifaces.status_code == 200
    assert "interfaces" in res_ifaces.get_json()


def test_speed_endpoints(test_client):
    res_status = test_client.get("/api/speed/status")
    assert res_status.status_code == 200
    data = res_status.get_json()
    assert "live" in data
    assert "latest" in data

    res_history = test_client.get("/api/speedtests")
    assert res_history.status_code == 200
    assert "rows" in res_history.get_json()


def test_storage_roots_and_browse(test_client, monkeypatch, tmp_path):
    sub = tmp_path / "browse_test"
    sub.mkdir()
    (sub / "hello.txt").write_text("world")

    monkeypatch.setattr(
        "systor.services.storage_service.safe_roots",
        lambda: [tmp_path]
    )
    monkeypatch.setattr(
        "systor.services.storage_service.auto_storage_roots",
        lambda: [str(tmp_path)]
    )
    monkeypatch.setattr(
        "systor.blueprints.storage.mount_payload",
        lambda p: {"mount": str(p), "total": 1000, "used": 500, "free": 500, "used_pct": 50.0}
    )

    res_mounts = test_client.get("/api/storage/mounts")
    assert res_mounts.status_code == 200
    assert "mounts" in res_mounts.get_json()

    res_browse = test_client.get(f"/api/storage/browse?path={sub}")
    assert res_browse.status_code == 200
    data = res_browse.get_json()
    assert data["ok"] is True
    assert any(entry["name"] == "hello.txt" for entry in data["entries"])


def test_security_headers_present(test_client):
    res = test_client.get("/health")
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
    assert res.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert "default-src 'self'" in res.headers.get("Content-Security-Policy", "")


def test_network_security_audit(test_client):
    res = test_client.get("/api/network/security")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert "listening_ports" in data
    assert "firewall" in data
    assert "fail2ban" in data
