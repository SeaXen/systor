"""Unit tests for hardware sensor telemetry (GPU, Raspberry Pi, and UPS)."""
from unittest.mock import MagicMock, patch

from systor.sensors import (
    get_gpu_metrics,
    get_rpi_metrics,
    get_ups_metrics,
    get_all_sensors,
)


def test_gpu_metrics_parsing():
    """Verify CSV output from nvidia-smi is parsed accurately."""
    mock_csv = "NVIDIA GeForce RTX 4090, 42.5, 8192.0, 24576.0, 54.0, 185.2\n"
    with patch("shutil.which", return_value="/usr/bin/nvidia-smi"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_csv)
            gpus = get_gpu_metrics()
            assert len(gpus) == 1
            g = gpus[0]
            assert g["name"] == "NVIDIA GeForce RTX 4090"
            assert g["gpu_util_pct"] == 42.5
            assert g["mem_used_mb"] == 8192.0
            assert g["mem_total_mb"] == 24576.0
            assert g["mem_used_pct"] == 33.3
            assert g["temp_c"] == 54.0
            assert g["power_w"] == 185.2


def test_gpu_metrics_missing_or_error():
    """Verify graceful empty return when nvidia-smi is absent or fails."""
    with patch("shutil.which", return_value=None):
        assert get_gpu_metrics() == []

    with patch("shutil.which", return_value="/usr/bin/nvidia-smi"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            assert get_gpu_metrics() == []


def test_rpi_metrics_throttling():
    """Verify Raspberry Pi throttling and undervoltage bitmask extraction."""
    with patch("shutil.which", return_value="/usr/bin/vcgencmd"):
        with patch("subprocess.run") as mock_run:
            # 0x50005 -> bits 0, 2, 16, 18 set (undervoltage and throttled now and past)
            mock_run.return_value = MagicMock(returncode=0, stdout="throttled=0x50005\n")
            rpi = get_rpi_metrics()
            assert rpi["present"] is True
            assert rpi["undervoltage"] is True
            assert rpi["throttled"] is True
            assert rpi["throttled_code"] == "0x50005"


def test_rpi_metrics_clean():
    """Verify clean state when vcgencmd returns 0x0."""
    with patch("shutil.which", return_value="/usr/bin/vcgencmd"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="throttled=0x0\n")
            rpi = get_rpi_metrics()
            assert rpi["present"] is True
            assert rpi["undervoltage"] is False
            assert rpi["throttled"] is False


def test_ups_metrics_parsing():
    """Verify NUT upsc client output parsing."""
    mock_ups_detail = """battery.charge: 100
battery.runtime: 1800
ups.load: 22.5
ups.status: OL
"""
    with patch("shutil.which", return_value="/usr/bin/upsc"):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="cyberpower\n"),
                MagicMock(returncode=0, stdout=mock_ups_detail),
            ]
            ups = get_ups_metrics()
            assert ups["present"] is True
            assert ups["name"] == "cyberpower"
            assert ups["battery_pct"] == 100.0
            assert ups["load_pct"] == 22.5
            assert ups["runtime_mins"] == 30.0
            assert ups["status"] == "OL"


def test_get_all_sensors_aggregation():
    """Verify aggregated sensors object and present flag."""
    with patch("systor.sensors.get_gpu_metrics", return_value=[{"id": 0, "name": "Test GPU"}]):
        with patch("systor.sensors.get_rpi_metrics", return_value={"present": False}):
            with patch("systor.sensors.get_ups_metrics", return_value={"present": False}):
                res = get_all_sensors(force_refresh=True)
                assert res["present"] is True
                assert len(res["gpu"]) == 1
                assert res["gpu"][0]["name"] == "Test GPU"
