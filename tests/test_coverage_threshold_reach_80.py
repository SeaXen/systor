import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import systor.metrics as metrics
from systor.web import create_app


def test_settings_form_exception_branches(tmp_path, monkeypatch):
    """Test POST /settings with invalid/boundary values to exercise except blocks."""
    app = create_app()
    app.config["TESTING"] = True

    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.blueprints.settings.save_config", lambda cfg: cfg_file)
    monkeypatch.setattr("systor.blueprints.settings.load_config", lambda: {
        "collector": {"poll_interval_sec": 5, "retention_days": 7, "rollup_retention_days": 90},
        "thresholds": {"cpu_load_1m": {"enabled": True, "value": 4.0, "duration_min": 2}},
        "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
        "discord": {"enabled": False, "webhook_url": ""},
        "web": {"host": "0.0.0.0", "port": 6677, "trusted_proxies": "127.0.0.1"},
        "auth": {"enabled": False, "username": "admin", "password_hash": ""},
        "network": {},
        "storage_page": {"max_scan_files": 20000},
        "speed": {},
    })

    with app.test_client() as client:
        # Invalid numeric strings for all fields to hit (ValueError, TypeError)
        bad_payload = {
            "cooldown_sec": "not-a-number",
            "poll_interval_sec": "not-a-number",
            "network_default_hours": "not-a-number",
            "network_auto_refresh_sec": "not-a-number",
            "network_default_bar_days": "not-a-number",
            "network_default_table_days": "not-a-number",
            "storage_max_scan_files": "not-a-number",
            "web_port": "not-a-number",
            "auth_idle_timeout_min": "not-a-number",
            "auth_max_fails": "not-a-number",
            "auth_cooldown_sec": "not-a-number",
            "th_val_cpu_load_1m": "not-a-number",
            "th_dur_cpu_load_1m": "not-a-number",
        }
        resp = client.post("/settings", data=bad_payload, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200

        # Password mismatch error (400)
        resp = client.post("/settings", data={
            "auth_password": "NewSecretPassword123!",
            "auth_password_confirm": "DifferentSecret!",
        }, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 400
        assert "does not match" in resp.get_json()["message"]

        # Auth enabled without password (400)
        resp = client.post("/settings", data={
            "auth_enabled": "1",
            "auth_username": "admin",
            "auth_clear_password": "1",
        }, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 400
        assert "password" in resp.get_json()["message"].lower()

        # Valid password setup
        resp = client.post("/settings", data={
            "auth_enabled": "1",
            "auth_username": "admin",
            "auth_password": "ValidPassword123!",
            "auth_password_confirm": "ValidPassword123!",
        }, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200


def test_storage_action_validation_branches(tmp_path, monkeypatch):
    """Test /api/storage/action validation error branches."""
    root = tmp_path / "storage_root"
    root.mkdir()
    (root / "test.txt").write_text("hello")

    cfg = {
        "storage_page": {
            "allowed_roots": str(root),
            "trash_enabled": False,  # Trash disabled to test delete error
            "public_readonly": False,
            "action_password_required": False,
            "max_scan_files": 1000,
        },
        "auth": {"enabled": False},
        "web": {"trusted_proxies": "127.0.0.1"},
    }
    monkeypatch.setattr("systor.blueprints.storage.load_config", lambda: cfg)
    monkeypatch.setattr("systor.services.storage_service.load_config", lambda: cfg)
    monkeypatch.setattr("systor.blueprints.common.load_config", lambda: cfg)

    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        # mkdir missing name
        resp = client.post("/api/storage/action", json={
            "action": "mkdir", "path": str(root), "name": ""
        }, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 400
        assert "missing folder name" in resp.get_json()["error"]

        # copy invalid destination
        resp = client.post("/api/storage/action", json={
            "action": "copy", "path": str(root), "items": [str(root / "test.txt")], "destination": str(root / "nonexistent")
        }, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 400

        # rename invalid items
        resp = client.post("/api/storage/action", json={
            "action": "rename", "path": str(root), "items": [], "name": "new.txt"
        }, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 400

        # delete when trash disabled
        resp = client.post("/api/storage/action", json={
            "action": "delete", "path": str(root), "items": [str(root / "test.txt")]
        }, headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 400
        assert "trash disabled" in resp.get_json()["error"]


def test_metrics_diskstats_and_netdev():
    """Test diskstats, netdev, and top processes by memory."""
    fake_diskstats = "   8       0 sda 100 200 5000 600 200 400 10000 800 0 1000 1400\n"
    fake_netdev = "Inter-|   Receive                                                |  Transmit\n face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n  eth0: 10485760     500    0    0    0     0          0         0  5242880     250    0    0    0     0       0          0\n"

    # read_disk_io_mbps
    with patch.object(Path, "read_text", return_value=fake_diskstats):
        # Call twice to compute delta
        metrics.read_disk_io_mbps(elapsed_sec=1.0)
        rates = metrics.read_disk_io_mbps(elapsed_sec=1.0)
        assert "read_mbps" in rates
        assert "write_mbps" in rates

    # read_network_stats
    with patch.object(Path, "read_text", return_value=fake_netdev):
        net_stats = metrics.read_network_stats()
        assert net_stats is not None
        assert net_stats["rx_bytes"] == 10485760
        assert net_stats["tx_bytes"] == 5242880

    # read_network_interfaces
    with patch.object(Path, "read_text", return_value=fake_netdev):
        ifaces = metrics.read_network_interfaces()
        assert len(ifaces) == 1
        assert ifaces[0]["iface"] == "eth0"

    # read_top_processes by mem
    with patch("os.listdir", return_value=["100", "200"]):
        with patch("systor.metrics._read_proc_stat") as mock_stat:
            mock_stat.side_effect = [
                ("proc1", 10, 10, 500, 1000),
                ("proc2", 20, 20, 1500, 2000),
            ]
            procs = metrics.read_top_processes(n=5, by="mem")
            assert len(procs) == 2
            assert procs[0]["mem_mb"] >= procs[1]["mem_mb"]


def test_smartctl_probing():
    """Test smart_service._probe_with_smartctl."""
    from systor.services.smart_service import _probe_with_smartctl

    mock_scan_out = json.dumps({"devices": [{"name": "/dev/sda"}]})
    mock_dev_out = json.dumps({
        "device": {"type": "ata", "protocol": "ATA"},
        "model_name": "Samsung SSD 860",
        "smart_status": {"passed": True},
        "temperature": {"current": 32},
        "power_on_time": {"hours": 1200},
    })

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=mock_scan_out),
            MagicMock(returncode=0, stdout=mock_dev_out),
        ]
        drives = _probe_with_smartctl("/usr/sbin/smartctl")
        assert len(drives) == 1
        assert drives[0]["model"] == "Samsung SSD 860"
        assert drives[0]["passed"] is True


def test_security_audit_service_tools():
    """Test firewall, fail2ban, and listening ports discovery."""
    from systor.services.security_audit_service import (
        get_firewall_status, get_fail2ban_status, get_listening_ports
    )

    # get_firewall_status
    with patch("shutil.which", return_value="/usr/sbin/ufw"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Status: active\n--\n22 ALLOW IN Anywhere\n")
            fw = get_firewall_status()
            assert fw["available"] is True
            assert fw["active"] is True
            assert fw["rules_count"] == 1

    # get_fail2ban_status
    with patch("shutil.which", return_value="/usr/bin/fail2ban-client"):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="Status\n|- Number of jail: 1\n`- Jail list: sshd\n"),
                MagicMock(returncode=0, stdout="Status for the jail: sshd\n|- Currently banned: 3\n`- Total banned: 15\n")
            ]
            f2b = get_fail2ban_status()
            assert f2b["available"] is True
            assert f2b["active"] is True
            assert f2b["banned_total"] == 18

    # get_listening_ports with ss
    ss_out = "Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\ntcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:((\"sshd\",pid=400,fd=3))\n"
    with patch("shutil.which", side_effect=lambda bin: "/bin/ss" if bin == "ss" else None):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=ss_out)
            ports = get_listening_ports()
            assert len(ports) == 1
            assert ports[0]["port"] == 22


def test_speed_blueprint_conflict_and_iperf():
    """Test /api/speed/live/start 409 and /api/speed/iperf/start."""
    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        # Start when already running -> 409
        with patch("systor.blueprints.speed.start_speed_live", return_value=False):
            resp = client.post("/api/speed/live/start", json={"server_id": "1"}, headers={"X-Forwarded-For": "127.0.0.1"})
            assert resp.status_code == 409
            assert "already running" in resp.get_json()["error"]

        # Start successful -> 200 and invokes on_success
        def mock_start(server_id, run_type, on_success):
            if on_success:
                on_success({"dl_mbps": 100.0, "ul_mbps": 50.0, "provider": "ookla"})
            return True

        with patch("systor.blueprints.speed.start_speed_live", side_effect=mock_start):
            with patch("time.sleep"):
                resp = client.post("/api/speed/live/start", json={"server_id": "1"}, headers={"X-Forwarded-For": "127.0.0.1"})
                assert resp.status_code == 200
                assert resp.get_json()["ok"] is True

        # /api/speed/iperf/start
        with patch("systor.blueprints.speed.start_iperf_server", return_value={"running": True, "started": True}):
            resp = client.post("/api/speed/iperf/start", headers={"X-Forwarded-For": "127.0.0.1"})
            assert resp.status_code == 200
            assert resp.get_json()["status"]["running"] is True

