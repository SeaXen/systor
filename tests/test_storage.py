"""Tests for Storage SQLite database: schemas, queries, retention purging, and rollups."""
import time
from pathlib import Path
import pytest

from systor.storage import Storage


@pytest.fixture
def store(tmp_path):
    db_file = tmp_path / "test_systor.db"
    return Storage(db_path=db_file, retention_days=7, rollup_retention_days=30)


def test_storage_init_creates_tables(store):
    st = store.stats()
    assert st["samples"] == 0
    assert st["alerts"] == 0
    assert st["speedtests"] == 0
    assert store.db_path.exists()


def test_insert_and_retrieve_samples(store):
    sample = {
        "ts": int(time.time()),
        "hostname": "srv-node1",
        "cpu": {"load_1m": 1.25, "load_5m": 0.85, "load_15m": 0.50, "percent": 25.4, "temp_c": 51.0},
        "memory": {"total_mb": 16000, "used_mb": 8000, "free_mb": 4000, "available_mb": 8000, "swap_used_mb": 100, "swap_total_mb": 2048},
        "disks": [{"mount": "/", "used_pct": 45, "used_gb": 45.0, "size_gb": 100.0}],
        "disk_io": {"read_mbps": 1.5, "write_mbps": 0.5},
        "network": {"rx_bytes": 1000000, "tx_bytes": 500000},
        "network_interfaces": [
            {"iface": "eth0", "rx_bytes": 1000000, "tx_bytes": 500000}
        ],
    }
    store.insert_sample(sample)

    rows = store.recent_samples(limit=10)
    assert len(rows) == 1
    assert rows[0]["hostname"] == "srv-node1"
    assert rows[0]["load_1m"] == 1.25
    assert rows[0]["cpu_pct"] == 25.4
    assert rows[0]["mem_used_mb"] == 8000

    # Series query
    pts = store.series("load_1m", hours=1)
    assert len(pts) == 1
    assert pts[0][1] == 1.25


def test_alerts_logging_and_retrieval(store):
    store.log_alert("cpu_load", "alert", 5.5, 4.0, "CPU load high")
    store.log_alert("cpu_load", "recover", 2.0, 4.0, "CPU load recovered")

    alerts = store.recent_alerts(limit=10)
    assert len(alerts) == 2
    assert alerts[0]["severity"] == "recover"
    assert alerts[1]["severity"] == "alert"
    assert alerts[1]["metric"] == "cpu_load"


def test_notification_logging(store):
    store.log_notification("telegram", True, None)
    store.log_notification("discord", False, "Webhook timeout 408")

    notifs = store.recent_notifications(limit=10)
    assert len(notifs) == 2
    channels = {n["channel"]: n["success"] for n in notifs}
    assert channels["telegram"] == 1
    assert channels["discord"] == 0


def test_speedtest_logging_and_filtering(store):
    store.log_speedtest({
        "ts": int(time.time()),
        "provider": "ookla",
        "target": "Local Fiber",
        "mode": "wan",
        "run_type": "manual",
        "ping_ms": 12.5,
        "dl_mbps": 520.4,
        "ul_mbps": 480.2,
        "ok": True,
    })
    store.log_speedtest({
        "ts": int(time.time()) - 10,
        "provider": "fast",
        "target": "Netflix CDN",
        "mode": "wan",
        "run_type": "auto",
        "ping_ms": 18.0,
        "dl_mbps": 300.0,
        "ul_mbps": 100.0,
        "ok": True,
    })

    # Retrieve all
    rows, total = store.recent_speedtests(limit=10)
    assert total == 2
    assert len(rows) == 2

    # Filter by provider
    rows_ookla, count_ookla = store.recent_speedtests(provider="ookla")
    assert count_ookla == 1
    assert rows_ookla[0]["provider"] == "ookla"

    # Filter by run_type
    rows_auto, count_auto = store.recent_speedtests(run_type="auto")
    assert count_auto == 1
    assert rows_auto[0]["provider"] == "fast"


def test_network_series_deltas(store):
    now = int(time.time())
    # 2 consecutive samples 10 seconds apart
    # dt = 10s, rx delta = 10 MB (10485760 bytes) -> rx_mbps = 1.0 MB/s
    store.insert_sample({
        "ts": now - 10,
        "network": {"rx_bytes": 10000000, "tx_bytes": 5000000},
    })
    store.insert_sample({
        "ts": now,
        "network": {"rx_bytes": 10000000 + 10485760, "tx_bytes": 5000000 + 5242880},
    })

    series = store.network_series(hours=1)
    assert len(series) == 1
    assert series[0]["rx_mbps"] == 1.0
    assert series[0]["tx_mbps"] == 0.5


def test_apply_retention_purges_old_data(store):
    now = int(time.time())
    old_ts = now - (10 * 86400)  # 10 days old (retention is 7 days)
    fresh_ts = now - (2 * 86400)  # 2 days old

    # Insert old and fresh samples
    store.insert_sample({"ts": old_ts, "cpu": {"percent": 50.0}})
    store.insert_sample({"ts": fresh_ts, "cpu": {"percent": 40.0}})

    st_before = store.stats()
    assert st_before["samples"] == 2

    # Run retention purge
    raw_deleted, rollup_deleted = store.apply_retention()
    assert raw_deleted >= 1

    st_after = store.stats()
    assert st_after["samples"] == 1
    assert st_after["oldest_sample_ts"] == fresh_ts
