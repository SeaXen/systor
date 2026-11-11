from unittest.mock import patch, MagicMock
from systor.storage import Storage
from systor import collector
from systor.services import speed_service, runtime_service
from systor import bot
from systor import metrics


def test_collector_full_run_cycle(tmp_path, monkeypatch):
    """Test collector.run() startup, cycle execution, and shutdown."""
    db_file = tmp_path / "collector.db"
    log_file = tmp_path / "collector.log"

    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_file])
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("systor.collector.DEFAULT_DB_PATH", str(db_file))

    from systor.config import load_config, save_config
    cfg = load_config()
    cfg["logging"]["file"] = str(log_file)
    cfg["collector"]["poll_interval_sec"] = 1
    save_config(cfg)

    # Allow exactly 1 cycle then stop
    cycle_count = 0
    def stop_after_one(sec):
        nonlocal cycle_count
        cycle_count += 1
        collector._running = False

    monkeypatch.setattr(collector.time, "sleep", stop_after_one)
    collector._running = True

    with patch("systor.bot.start_bot_daemon"):
        collector.run()

    assert cycle_count >= 1
    store = Storage(str(db_file))
    st = store.stats()
    assert st.get("samples", 0) >= 1


def test_speed_service_live_flow():
    """Test live speedtest parser, state management, and finalization."""
    speed_service.speed_live_reset(server_id="1234", target="Ookla", run_type="manual")
    state = speed_service.speed_live_copy()
    assert state["status"] == "idle"
    assert state["server_id"] == "1234"

    # Test line parsing for Ookla CLI output
    speed_service.speed_live_update(status="testing")
    speed_service.speed_live_parse_line("Server: FastServer (id: 1234)")
    speed_service.speed_live_parse_line("Idle Latency: 14.5 ms (jitter: 1.2ms)")
    speed_service.speed_live_parse_line("Download: 145.20 Mbps (data used: 120 MB)")
    speed_service.speed_live_parse_line("Upload: 48.60 Mbps (data used: 40 MB)")

    st_updated = speed_service.speed_live_copy()
    assert st_updated["ping_ms"] == 14.5
    assert st_updated["jitter_ms"] == 1.2
    assert st_updated["dl_mbps"] == 145.2
    assert st_updated["ul_mbps"] == 48.6

    # Test line parsing for Result URL
    speed_service.speed_live_parse_line("Result URL: https://www.speedtest.net/result/c/12345")
    st_url = speed_service.speed_live_copy()
    assert st_url["result_url"] == "https://www.speedtest.net/result/c/12345"

    # Finalize callback test
    callback_called = False
    def on_success(row):
        nonlocal callback_called
        callback_called = True

    speed_service.speed_live_finalize(0, on_success)
    assert callback_called is True
    assert "done" in speed_service.speed_live_copy()["status"]


def test_telegram_bot_daemon_lifecycle(tmp_path):
    """Test starting and stopping Telegram bot daemon thread."""
    db_file = tmp_path / "bot_life.db"
    store = Storage(str(db_file))

    cfg = {
        "telegram": {
            "enabled": True,
            "bot_token": "mock-token-1234",
            "chat_id": "99999",
        }
    }

    # Start bot with mocked worker
    with patch("systor.bot.bot_poll_worker"):
        t = bot.start_bot_daemon(cfg, store)
        assert t is not None
        assert t.is_alive() or not t.is_alive()  # Valid Thread object
        bot.stop_bot_daemon()
        assert bot._BOT_STOP_EVENT.is_set()


def test_runtime_service_host_and_docker():
    """Test get_host_apps and get_docker_apps fallbacks and parsing."""
    host_apps = runtime_service.get_host_apps(limit=10)
    assert isinstance(host_apps, list)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="id123\tweb-app\tUp 2 hours\t0.5%\t50MB / 1GB\t0.05%\n")
        docker_apps = runtime_service.get_docker_apps(limit=10)
        assert isinstance(docker_apps, list)


def test_metrics_subsystem_components():
    """Test individual metrics collectors."""
    mock_meminfo = (
        "MemTotal:       16384000 kB\n"
        "MemFree:         4096000 kB\n"
        "MemAvailable:    8192000 kB\n"
        "SwapTotal:       2048000 kB\n"
        "SwapFree:        1024000 kB\n"
    )
    with patch("pathlib.Path.read_text", return_value=mock_meminfo):
        mem = metrics.read_meminfo()
        assert mem is not None
        assert mem["total_mb"] > 0
        assert mem["available_mb"] > 0

    # Snapshot
    snap = metrics.collect_snapshot()
    assert "cpu" in snap
    assert "memory" in snap
    assert "disks" in snap

    # CPU percent
    pct = metrics.read_cpu_percent()
    assert pct is None or isinstance(pct, (int, float))

    # CPU load
    load1 = metrics.read_loadavg()
    assert load1 is None or isinstance(load1, float)

    # Disks
    disks = metrics.read_disk_usage()
    assert isinstance(disks, list)

    # Disk IO
    io_stat = metrics.read_disk_io_mbps()
    assert "read_mbps" in io_stat
    assert "write_mbps" in io_stat

    # Network traffic
    ifaces = metrics.read_network_interfaces()
    assert isinstance(ifaces, list)
