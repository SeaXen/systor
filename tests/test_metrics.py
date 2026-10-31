"""Tests for Linux /proc, /sys metric readers and delta computations."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from systor import metrics


def test_read_loadavg(monkeypatch, tmp_path):
    proc_file = tmp_path / "loadavg"
    proc_file.write_text("1.75 0.92 0.45 2/350 4912\n")

    monkeypatch.setattr("systor.metrics.Path", lambda p: proc_file if p == "/proc/loadavg" else Path(p))
    assert metrics.read_loadavg() == 1.75


def test_read_loadavg_missing():
    # If /proc/loadavg is unavailable or raises, return None gracefully
    with patch("systor.metrics.Path") as mock_path:
        mock_path.side_effect = FileNotFoundError
        assert metrics.read_loadavg() is None


def test_cpu_percent_calculation(monkeypatch, tmp_path):
    # Test _stat_total_idle and delta math
    proc_stat = tmp_path / "stat"
    # cpu: user nice system idle iowait irq softirq steal guest guest_nice
    # sample 1: total = 100+0+50+800+50 = 1000, idle = 800+50 = 850 (active = 150)
    proc_stat.write_text("cpu  100 0 50 800 50 0 0 0 0 0\n")

    monkeypatch.setattr("systor.metrics.Path", lambda p: proc_stat if p == "/proc/stat" else Path(p))
    metrics._prev_cpu_stat = None

    # First call initializes baseline, returns None
    val1 = metrics._cpu_percent_delta()
    assert val1 is None

    # sample 2: total = 200+0+100+1600+100 = 2000, idle = 1600+100 = 1700
    # dt = 1000, di = 850, active_delta = 150 -> 15.0%
    proc_stat.write_text("cpu  200 0 100 1600 100 0 0 0 0 0\n")
    val2 = metrics._cpu_percent_delta()
    assert val2 == 15.0


def test_read_cpu_temp_c(monkeypatch, tmp_path):
    # Mock thermal zones: thermal_zone0 (42500 -> 42.5 C), thermal_zone1 (58000 -> 58.0 C)
    tz0 = tmp_path / "thermal_zone0"
    tz0.mkdir()
    (tz0 / "temp").write_text("42500\n")

    tz1 = tmp_path / "thermal_zone1"
    tz1.mkdir()
    (tz1 / "temp").write_text("58000\n")

    monkeypatch.setattr(
        "systor.metrics.Path",
        lambda p: tmp_path if p == "/sys/class/thermal" else Path(p)
    )

    temp = metrics.read_cpu_temp_c()
    assert temp == 58.0


def test_read_cpu_temp_c_none_available(monkeypatch, tmp_path):
    empty_dir = tmp_path / "thermal"
    empty_dir.mkdir()
    monkeypatch.setattr(
        "systor.metrics.Path",
        lambda p: empty_dir if p == "/sys/class/thermal" else Path(p)
    )
    assert metrics.read_cpu_temp_c() is None


def test_read_meminfo(monkeypatch, tmp_path):
    meminfo_file = tmp_path / "meminfo"
    meminfo_file.write_text(
        "MemTotal:       16384000 kB\n"
        "MemFree:         4096000 kB\n"
        "MemAvailable:    8192000 kB\n"
        "Buffers:          512000 kB\n"
        "Cached:          4096000 kB\n"
        "SwapTotal:       2097152 kB\n"
        "SwapFree:        1048576 kB\n"
    )

    monkeypatch.setattr("systor.metrics.Path", lambda p: meminfo_file if p == "/proc/meminfo" else Path(p))
    mem = metrics.read_meminfo()

    assert mem is not None
    assert mem["total_mb"] == 16000
    assert mem["free_mb"] == 4000
    assert mem["available_mb"] == 8000
    assert mem["used_mb"] == 8000
    assert mem["swap_total_mb"] == 2048
    assert mem["swap_used_mb"] == 1024


def test_read_disk_usage(monkeypatch):
    mock_df_out = (
        "Filesystem     1G-blocks  Used Available Use% Mounted on\n"
        "/dev/sda1           100G   40G       60G  40% /\n"
        "/dev/sdb1          1000G  500G      500G  50% /mnt/data\n"
        "tmpfs                16G    1G       15G   7% /run\n"
    )

    mock_run = MagicMock()
    mock_run.return_value.stdout = mock_df_out
    monkeypatch.setattr("subprocess.run", mock_run)

    disks = metrics.read_disk_usage()
    assert len(disks) == 2  # /run was skipped as pseudo filesystem
    assert disks[0]["mount"] == "/"
    assert disks[0]["used_pct"] == 40
    assert disks[0]["size_gb"] == 100.0
    assert disks[1]["mount"] == "/mnt/data"
    assert disks[1]["used_pct"] == 50


def test_read_network_stats(monkeypatch, tmp_path):
    net_dev = tmp_path / "net_dev"
    net_dev.write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "    lo: 1000000   1000    0    0    0     0          0         0  1000000   1000    0    0    0     0       0          0\n"
        "  eth0: 5000000   2000    0    0    0     0          0         0  2000000   1500    0    0    0     0       0          0\n"
    )

    monkeypatch.setattr("systor.metrics.Path", lambda p: net_dev if p == "/proc/net/dev" else Path(p))
    stats = metrics.read_network_stats()
    assert stats is not None
    assert stats["rx_bytes"] == 5000000
    assert stats["tx_bytes"] == 2000000


def test_read_network_interfaces(monkeypatch, tmp_path):
    net_dev = tmp_path / "net_dev"
    net_dev.write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "  eth0: 10485760   2000    0    0    0     0          0         0  5242880   1500    0    0    0     0       0          0\n"
    )
    monkeypatch.setattr("systor.metrics.Path", lambda p: net_dev if p == "/proc/net/dev" else Path(p))
    metrics._prev_net_ifaces = {}
    metrics._last_net_ts = 0.0

    # First call sets baseline
    rows1 = metrics.read_network_interfaces()
    assert len(rows1) == 1
    assert rows1[0]["iface"] == "eth0"
    assert rows1[0]["rx_bytes"] == 10485760


def test_read_uptime_sec(monkeypatch, tmp_path):
    uptime_file = tmp_path / "uptime"
    uptime_file.write_text("3600.52 7100.12\n")
    monkeypatch.setattr("systor.metrics.Path", lambda p: uptime_file if p == "/proc/uptime" else Path(p))
    assert metrics.read_uptime_sec() == 3600


def test_read_disk_io_mbps(monkeypatch, tmp_path):
    diskstats = tmp_path / "diskstats"
    # dev sda: 8 0 sda 100 0 20480 100 200 0 40960 200 0 0 0 ...
    diskstats.write_text("   8       0 sda 100 0 20480 100 200 0 40960 200 0 0 0\n")
    monkeypatch.setattr("systor.metrics.Path", lambda p: diskstats if p == "/proc/diskstats" else Path(p))

    metrics._prev_disk = {}
    metrics._last_disk_ts = 0.0

    # Initial call sets baseline
    res1 = metrics.read_disk_io_mbps()
    assert res1["read_mbps"] == 0.0
    assert res1["write_mbps"] == 0.0
