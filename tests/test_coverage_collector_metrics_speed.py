import json
import time
from unittest.mock import patch, MagicMock

import systor.collector as collector
from systor.metrics import (
    _read_proc_stat, _read_proc_cmdline, read_total_memory_mb, read_system_updates,
    read_cpu_percent
)
from systor.speed import (
    list_ookla_servers, run_ookla, list_librespeed_servers,
    run_librespeed, iperf_status, local_ipv4s, speed_alert_triggered, build_speed_alert
)
from systor.storage import Storage


def test_metrics_linux_procs(monkeypatch):
    """Test process and system metric parsers with mocked files."""
    # Test read_cpu_percent
    assert read_cpu_percent(None, None) is None

    # Test _read_proc_stat parsing
    # Simulated stat: 1234 (python3) S 1 1234 1234 0 -1 4194304 1000 0 0 0 150 50 0 0 20 0 1 0 100000 5000 2500 ...
    fake_stat = b"1234 (python3 worker) S 1 1234 1234 0 -1 4194304 1000 0 0 0 150 50 0 0 20 0 1 0 100000 5000 2500 18446744073709551615 0 0 0 0 0 0 0 0 0 0 0 0 17 0 0 0 0 0 0\n"

    with patch("builtins.open") as mock_open:
        mock_file = MagicMock()
        mock_file.__enter__.return_value.read.return_value = fake_stat
        mock_open.return_value = mock_file

        stat_res = _read_proc_stat(1234)
        assert stat_res is not None
        comm, utime, stime, rss, start = stat_res
        assert comm == "python3 worker"
        assert utime == 150
        assert stime == 50
        assert rss == 2500

    # Test _read_proc_cmdline
    with patch("builtins.open") as mock_open:
        mock_file = MagicMock()
        mock_file.__enter__.return_value.read.return_value = b"python3\x00-m\x00systor\x00serve\x00"
        mock_open.return_value = mock_file
        cmd = _read_proc_cmdline(1234)
        assert cmd == "python3 -m systor serve"

    # Test read_total_memory_mb
    with patch("builtins.open") as mock_open:
        mock_file = MagicMock()
        mock_file.__enter__.return_value = ["MemTotal:       16384000 kB\n"]
        mock_open.return_value = mock_file
        tot_mb = read_total_memory_mb()
        assert tot_mb == 16000

    # Test read_system_updates
    updates = read_system_updates()
    assert "reboot_required" in updates


def test_speed_providers_mocked():
    """Test Ookla, LibreSpeed, and iPerf runners with mocked CLI output."""
    # list_ookla_servers
    with patch("systor.speed._cmd_exists", return_value=True):
        with patch("systor.speed._run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"servers": [{"id": 1234, "name": "Test Server", "location": "Dallas", "country": "US"}]})
            )
            servers = list_ookla_servers()
            assert len(servers) == 1
            assert servers[0]["id"] == "1234"

    # run_ookla
    ookla_json = json.dumps({
        "server": {"id": 1234, "name": "Dallas Server", "location": "Dallas", "country": "US", "host": "dallas.speedtest.net"},
        "ping": {"latency": 15.2, "jitter": 1.1},
        "download": {"bandwidth": 12500000},  # 100 Mbps
        "upload": {"bandwidth": 6250000},     # 50 Mbps
        "packetLoss": 0.0,
    })
    with patch("systor.speed._cmd_exists", return_value=True):
        with patch("systor.speed._run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=ookla_json, stderr="")
            res = run_ookla(server_id="1234")
            assert res["ok"] is True
            assert res["dl_mbps"] == 100.0
            assert res["ul_mbps"] == 50.0

    # list_librespeed_servers
    librespeed_out = "1: Frankfurt, Germany (https://fra.speed.net)\n2: New York, USA (https://nyc.speed.net)\n"
    with patch("systor.speed._cmd_exists", return_value=True):
        with patch("systor.speed._run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=librespeed_out)
            l_servers = list_librespeed_servers()
            assert len(l_servers) == 2
            assert l_servers[0]["id"] == "1"

    # run_librespeed
    librespeed_json = json.dumps([{
        "server": {"name": "Frankfurt", "url": "https://fra.speed.net"},
        "ping": 20.5, "jitter": 2.1,
        "download": 85.4, "upload": 42.1
    }])
    with patch("systor.speed._cmd_exists", return_value=True):
        with patch("systor.speed._run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=librespeed_json, stderr="")
            l_res = run_librespeed(server_id="1")
            assert l_res["ok"] is True
            assert l_res["dl_mbps"] == 85.4

    # iperf_status and start_iperf_server
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="LISTEN 0 128 0.0.0.0:5201 users:((\"iperf3\",pid=123))\n")
        st = iperf_status(5201)
        assert st["running"] is True

    # local_ipv4s
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="2: eth0:\n    inet 192.168.1.150/24 brd 192.168.1.255 scope global eth0\n")
        ips = local_ipv4s()
        assert "192.168.1.150" in ips

    # speed_alert_triggered and build_speed_alert
    cfg = {"speed": {"min_download_mbps": 50.0, "min_upload_mbps": 20.0}}
    bad_res = {"dl_mbps": 10.0, "ul_mbps": 5.0, "provider": "ookla", "target": "test"}
    triggered, reason = speed_alert_triggered(bad_res, cfg)
    assert triggered is True
    assert "DL 10.00 < 50.00" in reason

    subj, body = build_speed_alert(bad_res, "my-server")
    assert "Speed alert" in subj
    assert "my-server" in body


def test_collector_loop_execution(tmp_path, monkeypatch):
    """Test collector run() loop: snapshot insertion, alerts, hot-reload, retention."""
    db_file = tmp_path / "collector_test.db"
    store = Storage(str(db_file))

    cfg = {
        "collector": {
            "poll_interval_sec": 1,
            "retention_days": 1,
            "rollup_retention_days": 7,
        },
        "thresholds": {
            "cooldown_sec": 10,
            "cpu_load_1m": {"value": 5.0, "duration_min": 0, "enabled": True},
            "cpu_temp_c": {"value": 70.0, "duration_min": 0, "enabled": True},
            "mem_free_mb": {"value": 200, "duration_min": 0, "enabled": True},
            "swap_used_mb": {"value": 100, "duration_min": 0, "enabled": True},
            "disk_used_pct": {"value": 85.0, "duration_min": 0, "enabled": True},
        },
        "speed": {
            "auto_enabled": True,
            "auto_interval_min": 1,
            "auto_provider": "ookla",
            "notify_enabled": True,
            "min_download_mbps": 50.0,
            "min_upload_mbps": 20.0,
        },
        "notifications": {
            "enabled": False,
        }
    }

    monkeypatch.setattr("systor.collector.load_config", lambda: cfg)
    monkeypatch.setattr("systor.collector.Storage", lambda **kwargs: store)
    monkeypatch.setattr("systor.collector.DEFAULT_DB_PATH", db_file)

    # Mock snapshot to trigger alerts
    sample_snap = {
        "ts": int(time.time()),
        "hostname": "alert-test-node",
        "cpu": {"load_1m": 10.5, "temp_c": 82.0},
        "memory": {"available_mb": 50, "swap_used_mb": 500},
        "disks": [{"mount": "/", "used_pct": 95.0}],
    }
    monkeypatch.setattr("systor.collector.collect_snapshot", lambda: sample_snap)

    # Mock speed run
    speed_result = {
        "ts": int(time.time()), "provider": "ookla", "mode": "wan", "ok": True,
        "dl_mbps": 10.0, "ul_mbps": 5.0, "ping_ms": 150.0,
        "target": "server 1", "server_id": "1", "note": ""
    }
    monkeypatch.setattr("systor.collector.run_provider", lambda *args, **kwargs: speed_result)

    # Run exactly one iteration of collector.run by stopping in time.sleep
    def mock_sleep(sec):
        collector._running = False

    collector._running = True
    collector._reload_requested = True
    with patch("time.sleep", side_effect=mock_sleep):
        collector.run()

    # Verify snapshot and alerts were recorded
    samples = store.recent_samples(limit=1)
    assert len(samples) > 0
    assert samples[0]["hostname"] == "alert-test-node"

    alerts = store.recent_alerts(limit=10)
    assert len(alerts) > 0

    # Test signal handlers
    import pytest
    from systor.web import _handle_term as web_handle_term
    collector._handle_term(15, None)
    assert collector._running is False
    collector._handle_hup(1, None)
    assert collector._reload_requested is True
    with pytest.raises(SystemExit):
        web_handle_term(15, None)
