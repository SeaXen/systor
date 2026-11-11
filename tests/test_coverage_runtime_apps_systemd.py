import json
import subprocess
from unittest.mock import patch, MagicMock

from systor.services.runtime_service import (
    _parse_docker_size_mb, get_docker_apps, find_exact_processes,
    pid_stats, file_size_mb, systor_storage_state, get_runtime_status,
    get_compose_stacks, restart_compose_project, _docker_cache
)
from systor.services.systemd_service import (
    get_allowed_units, is_unit_allowed, list_systemd_services, restart_systemd_service
)
from systor.storage import Storage
from systor.web import create_app


def test_runtime_service_helpers(tmp_path):
    """Test parse_docker_size_mb and process/storage helpers."""
    # Size parsing
    assert _parse_docker_size_mb("100 B") == round(100 / (1024 * 1024), 3)
    assert _parse_docker_size_mb("50 KiB") == round(50 / 1024, 3)
    assert _parse_docker_size_mb("128 MiB") == 128.0
    assert _parse_docker_size_mb("2 GiB") == 2048.0
    assert _parse_docker_size_mb("1 TiB") == 1024 * 1024
    assert _parse_docker_size_mb("invalid") == 0.0
    assert _parse_docker_size_mb("") == 0.0

    # file_size_mb
    f = tmp_path / "test.bin"
    f.write_bytes(b"x" * (1024 * 1024 * 2))
    assert file_size_mb(f) == 2.0
    assert file_size_mb(tmp_path / "nonexistent") == 0.0

    # systor_storage_state
    st = systor_storage_state()
    assert "db_mb" in st
    assert "log_mb" in st
    assert "total_mb" in st

    # pid_stats
    assert pid_stats(None) == {"cpu_percent": None, "rss_mb": None, "vsz_mb": None}
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="12.5 10240 20480\n")
        res = pid_stats(1234)
        assert res["cpu_percent"] == 12.5
        assert res["rss_mb"] == 10.0
        assert res["vsz_mb"] == 20.0

    # find_exact_processes
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="100 3600 python3 -m systor serve web\n200 1800 python3 -m systor serve collector\n"
        )
        procs = find_exact_processes(["python3 -m systor serve web"])
        assert len(procs) == 1
        assert procs[0]["pid"] == 100
        assert procs[0]["uptime_sec"] == 3600

    # get_runtime_status
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="1 100 comm args\n")
        r_status = get_runtime_status()
        assert r_status["ok"] is True
        assert "web" in r_status
        assert "collector" in r_status


def test_docker_apps_and_compose(monkeypatch):
    """Test get_docker_apps, get_compose_stacks, and restart_compose_project."""
    _docker_cache["ts"] = 0.0
    _docker_cache["rows"] = []
    # get_docker_apps with simulated docker stats
    stats_output = "c123456789ab\tweb-app\t15.5%\t256MiB / 1GiB\t1.2MB / 3.4MB\t10MB / 20MB\n"
    with patch("subprocess.run") as mock_run:
        # First call checks command -v docker, second call runs docker stats
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout=stats_output)
        ]
        apps = get_docker_apps(limit=10)
        assert len(apps) == 1
        assert apps[0]["name"] == "web-app"
        assert apps[0]["cpu_percent"] == 15.5
        assert apps[0]["mem_mb"] == 256.0

    # get_compose_stacks
    compose_ps_out = "c123456789ab\tweb-app\tmy-stack\tweb\tUp 2 hours\n"
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=compose_ps_out)
            with patch("systor.services.runtime_service.get_docker_apps", return_value=[]):
                stacks = get_compose_stacks()
                assert len(stacks) == 1
                assert stacks[0]["project"] == "my-stack"
                assert len(stacks[0]["containers"]) == 1

    # restart_compose_project
    monkeypatch.setattr("systor.config.load_config", lambda: {"apps": {"docker_control": True}})

    # Invalid project name
    ok, msg = restart_compose_project("bad; injection!")
    assert ok is False
    assert "Invalid project name" in msg

    # Control disabled
    monkeypatch.setattr("systor.config.load_config", lambda: {"apps": {"docker_control": False}})
    ok, msg = restart_compose_project("valid-stack")
    assert ok is False
    assert "disabled" in msg

    # Successful restart
    monkeypatch.setattr("systor.config.load_config", lambda: {"apps": {"docker_control": True}})
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="cid1\ncid2\n"),  # docker ps -q
                MagicMock(returncode=0, stderr=""),             # docker restart
            ]
            ok, msg = restart_compose_project("my-stack")
            assert ok is True
            assert "Successfully restarted 2 container(s)" in msg


def test_systemd_service_deep(tmp_path, monkeypatch):
    """Test get_allowed_units, list_systemd_services JSON/tabular, restart_systemd_service."""
    # get_allowed_units with various config formats
    monkeypatch.setattr("systor.services.systemd_service.load_config", lambda: {"systemd": {"allowed_units": "['nginx.service', 'systor-*.service']"}})
    units = get_allowed_units()
    assert "nginx.service" in units

    monkeypatch.setattr("systor.services.systemd_service.load_config", lambda: {"systemd": {"allowed_units": "app.service, web.service"}})
    units2 = get_allowed_units()
    assert "app.service" in units2

    assert is_unit_allowed("systor-web.service", ["systor-*.service"]) is True
    assert is_unit_allowed("malicious.service", ["systor-*.service"]) is False

    # list_systemd_services JSON branch
    with patch("shutil.which", return_value="/bin/systemctl"):
        with patch("subprocess.run") as mock_run:
            json_out = json.dumps([{"unit": "systor-web.service", "load": "loaded", "active": "active", "sub": "running", "description": "Systor Web"}])
            mock_run.return_value = MagicMock(returncode=0, stdout=json_out)
            res = list_systemd_services()
            assert res["available"] is True
            assert len(res["services"]) == 1

    # list_systemd_services tabular fallback
    with patch("shutil.which", return_value="/bin/systemctl"):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                Exception("JSON unsupported"),  # first call fails
                MagicMock(returncode=0, stdout="systor-web.service loaded active running Systor Web Daemon\n")
            ]
            res = list_systemd_services()
            assert res["available"] is True
            assert len(res["services"]) == 1
            assert res["services"][0]["unit"] == "systor-web.service"

    # restart_systemd_service
    db_file = tmp_path / "systemd.db"
    store = Storage(str(db_file))
    user = "admin"
    ip = "192.168.1.100"

    # Not allowed
    ok, msg = restart_systemd_service("unauthorized.service", user, ip, store)
    assert ok is False
    assert "allow-list" in msg

    # TimeoutExpired
    with patch("systor.services.systemd_service.is_unit_allowed", return_value=True):
        with patch("shutil.which", return_value="/bin/systemctl"):
            with patch("systor.services.systemd_service._last_restart_by_ip", {}):
                with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=10)):
                    ok, msg = restart_systemd_service("systor-web.service", user, ip, store)
                    assert ok is False
                    assert "Timeout" in msg


def test_apps_blueprint_logs_and_terminal(monkeypatch):
    """Test /api/apps/logs/stream SSE, and terminal endpoints."""
    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        # /api/apps/logs/stream - invalid unit name
        resp = client.get("/api/apps/logs/stream?unit=bad;name", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 400

        # /api/apps/logs/stream - invalid container ID
        resp = client.get("/api/apps/logs/stream?container=bad%20id!", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 400

        # /api/apps/logs/stream - valid stream with mocked process
        with patch("shutil.which", return_value="/usr/bin/journalctl"):
            mock_proc = MagicMock()
            mock_proc.stdout = iter(["line 1\n", "line 2\n"])
            with patch("subprocess.Popen", return_value=mock_proc):
                resp = client.get("/api/apps/logs/stream?unit=systor-web.service&lines=10", headers={"X-Forwarded-For": "127.0.0.1"})
                assert resp.status_code == 200
                data = resp.get_data(as_text=True)
                assert "connected" in data
                assert "line 1" in data

        # /api/terminal/auth - bad password
        monkeypatch.setattr("systor.blueprints.apps.load_config", lambda: {
            "web": {"trusted_proxies": "127.0.0.1"},
            "terminal": {"enabled": True},
            "auth": {"password_hash": "somehash"}
        })
        resp = client.post("/api/terminal/auth", json={"password": "wrongpassword"}, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 401

        # /api/terminal/exec - invalid token
        resp = client.post("/api/terminal/exec", json={"token": "fake-token", "command": "uptime"}, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 400
