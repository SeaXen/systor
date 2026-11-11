import json
from unittest.mock import patch, MagicMock
from systor.collector import (
    _eval_metric, _fmt, _build_alert_body, _samples_for, _th_metric
)
from systor import speed


def test_collector_metric_evaluation_and_formatting():
    """Test collector threshold evaluation logic and formatting helpers."""
    # Value below threshold (no alert)
    res = _eval_metric("cpu_pct", 50.0, 80.0, sustained_needed=1, higher_is_worse=True, cooldown_sec=300)
    assert res is None

    # Value above threshold (triggers alert)
    res_alert = _eval_metric("cpu_pct", 95.0, 80.0, sustained_needed=1, higher_is_worse=True, cooldown_sec=300)
    assert res_alert is not None
    action, val, th = res_alert
    assert action == "alert"
    assert val == 95.0

    # Cooldown prevents immediate re-alerting
    res_cooldown = _eval_metric("cpu_pct", 96.0, 80.0, sustained_needed=1, higher_is_worse=True, cooldown_sec=300)
    assert res_cooldown is None

    # Recovery alert
    res_rec = _eval_metric("cpu_pct", 40.0, 80.0, sustained_needed=1, higher_is_worse=True, cooldown_sec=300)
    assert res_rec is not None
    assert res_rec[0] == "recover"

    # Formatting
    assert "95.0" in _fmt("cpu_pct", 95.0, 80.0)

    # Samples calculation
    assert _samples_for(5, 10) == 30

    # Th metric unpacking
    val, dur, enabled = _th_metric({"cpu_pct": {"enabled": True, "value": 85, "duration_min": 3}}, "cpu_pct")
    assert enabled is True
    assert val == 85
    assert dur == 3


def test_collector_alert_body_builder():
    """Test constructing alert notification body."""
    snap = {
        "hostname": "test-box",
        "cpu": {"load_1m": 4.5, "load_5m": 3.2, "load_15m": 2.1, "temp_c": 65},
        "memory": {"used_mb": 14000, "total_mb": 16000, "free_mb": 2000, "available_mb": 3000, "swap_used_mb": 100},
        "disks": [{"mount": "/", "used_pct": 92}],
    }
    body = _build_alert_body("ALERT", "cpu_pct", "alert", 95.0, 80.0, snap, worst_disk=snap["disks"][0], msg="CPU critical")
    assert "ALERT" in body
    assert "test-box" in body
    assert "CPU critical" in body


def test_speed_probe_and_calculations():
    """Test speed testing helpers, float conversions, and alert conditions."""
    assert speed._safe_float("42.5") == 42.5
    assert speed._safe_float("invalid") is None
    assert speed._safe_int("100") == 100
    assert speed._safe_int("bad") is None

    # Test HTTP probe with mocked safe_urlopen
    with patch("systor.speed.safe_urlopen") as mock_url:
        mock_r = MagicMock()
        mock_r.status = 200
        mock_r.headers.get.return_value = "262144"
        mock_r.read.return_value = b"x" * 262144
        mock_r.__enter__.return_value = mock_r
        mock_url.return_value = mock_r

        probe_res = speed.run_cloudflare_probe()
        assert probe_res["ok"] is True
        assert probe_res["dl_mbps"] > 0
        assert probe_res["provider"] == "cloudflare"

    # Speed alert trigger check
    res_slow = {"ok": True, "dl_mbps": 5.0, "ul_mbps": 10.0, "ping_ms": 20.0, "provider": "ookla"}
    cfg_threshold = {"speed": {"min_download_mbps": 20.0}}
    triggered, reason = speed.speed_alert_triggered(res_slow, cfg_threshold)
    assert triggered is True
    assert "DL" in reason

def test_speed_cli_parsers_and_iperf():
    """Test Ookla, LibreSpeed, iperf, and local IP detection."""
    # Mock Ookla output
    mock_ookla_json = json.dumps({
        "type": "result",
        "ping": {"latency": 12.3, "jitter": 1.1},
        "download": {"bandwidth": 12500000},  # 100 Mbps
        "upload": {"bandwidth": 6250000},    # 50 Mbps
        "packetLoss": 0.0,
        "server": {"id": 1234, "name": "SpeedServer", "location": "City", "country": "Country"},
    })

    with patch("systor.speed._cmd_exists", return_value=True):
        with patch("systor.speed._run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_ookla_json, stderr="")
            res_ookla = speed.run_ookla(server_id=1234)
            assert res_ookla["ok"] is True
            assert res_ookla["dl_mbps"] == 100.0
            assert res_ookla["ul_mbps"] == 50.0
            assert res_ookla["ping_ms"] == 12.3

    # Mock LibreSpeed output
    mock_libre_json = json.dumps([{
        "ping": 18.5,
        "jitter": 2.0,
        "download": 75.5,
        "upload": 25.2,
        "server": {"id": 5, "name": "LibreNode"},
    }])
    with patch("systor.speed._cmd_exists", return_value=True):
        with patch("systor.speed._run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_libre_json, stderr="")
            res_libre = speed.run_librespeed(server_id=5)
            assert res_libre["ok"] is True
            assert res_libre["dl_mbps"] == 75.5
            assert res_libre["ul_mbps"] == 25.2

    # iperf status & local IPs
    ip_list = speed.local_ipv4s()
    assert isinstance(ip_list, list)

    st_iperf = speed.iperf_status(port=5201)
    assert "running" in st_iperf
