"""SQLite storage layer with retention and rollup."""
from __future__ import annotations
import sqlite3
import time
import json
import threading
from pathlib import Path
from contextlib import contextmanager

DEFAULT_DB_PATH = Path("/var/lib/systor/systor.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts        INTEGER NOT NULL,
    load_1m   REAL,
    load_5m   REAL,
    load_15m  REAL,
    cpu_pct   REAL,
    cpu_temp  REAL,
    mem_total_mb    INTEGER,
    mem_used_mb     INTEGER,
    mem_free_mb     INTEGER,
    mem_avail_mb    INTEGER,
    swap_used_mb    INTEGER,
    swap_total_mb   INTEGER,
    disk_used_pct   REAL,
    disk_used_gb    REAL,
    disk_size_gb    REAL,
    disk_mount      TEXT,
    net_rx_bytes    INTEGER,
    net_tx_bytes    INTEGER,
    hostname        TEXT
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);

CREATE TABLE IF NOT EXISTS rollups_5m (
    ts        INTEGER NOT NULL,
    load_1m_avg   REAL, load_1m_max REAL,
    cpu_pct_avg   REAL, cpu_pct_max REAL,
    cpu_temp_avg  REAL, cpu_temp_max REAL,
    mem_used_mb_avg REAL, mem_used_mb_max INTEGER,
    swap_used_mb_avg INTEGER, swap_used_mb_max INTEGER
);
CREATE INDEX IF NOT EXISTS idx_rollups_5m_ts ON rollups_5m(ts);

CREATE TABLE IF NOT EXISTS alerts (
    ts        INTEGER NOT NULL,
    metric    TEXT NOT NULL,
    severity  TEXT NOT NULL,  -- 'alert' | 'recover'
    value     REAL,
    threshold REAL,
    message   TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);

CREATE TABLE IF NOT EXISTS notification_log (
    ts        INTEGER NOT NULL,
    channel   TEXT NOT NULL,  -- 'telegram' | 'discord'
    success   INTEGER NOT NULL, -- 0/1
    error     TEXT
);
"""


class Storage:
    """Thread-safe SQLite wrapper. Auto-creates schema and applies retention."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH, retention_days: int = 7, rollup_retention_days: int = 90):
        self.db_path = db_path
        self.retention_days = retention_days
        self.rollup_retention_days = rollup_retention_days
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    def insert_sample(self, s: dict) -> None:
        with self._lock, self._conn() as c:
            # Worst disk
            worst_disk = max(s.get("disks", []), key=lambda d: d.get("used_pct", 0), default={})
            mem = s.get("memory", {}) or {}
            net = s.get("network", {}) or {}
            cpu = s.get("cpu", {}) or {}
            c.execute(
                """INSERT INTO samples
                (ts, load_1m, load_5m, load_15m, cpu_pct, cpu_temp,
                 mem_total_mb, mem_used_mb, mem_free_mb, mem_avail_mb,
                 swap_used_mb, swap_total_mb,
                 disk_used_pct, disk_used_gb, disk_size_gb, disk_mount,
                 net_rx_bytes, net_tx_bytes, hostname)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    s.get("ts", int(time.time())),
                    cpu.get("load_1m"), cpu.get("load_5m"), cpu.get("load_15m"),
                    cpu.get("percent"), cpu.get("temp_c"),
                    mem.get("total_mb"), mem.get("used_mb"), mem.get("free_mb"), mem.get("available_mb"),
                    mem.get("swap_used_mb"), mem.get("swap_total_mb"),
                    worst_disk.get("used_pct"), worst_disk.get("used_gb"), worst_disk.get("size_gb"), worst_disk.get("mount"),
                    net.get("rx_bytes"), net.get("tx_bytes"),
                    s.get("hostname"),
                ),
            )

    def log_alert(self, metric: str, severity: str, value: float | None, threshold: float | None, message: str) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO alerts (ts, metric, severity, value, threshold, message) VALUES (?,?,?,?,?,?)",
                (int(time.time()), metric, severity, value, threshold, message),
            )

    def log_notification(self, channel: str, success: bool, error: str | None = None) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO notification_log (ts, channel, success, error) VALUES (?,?,?,?)",
                (int(time.time()), channel, 1 if success else 0, error or ""),
            )

    def recent_samples(self, limit: int = 200) -> list[dict]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT * FROM samples WHERE disk_mount = (SELECT disk_mount FROM samples ORDER BY ts DESC LIMIT 1) OR disk_mount IS NULL ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def series(self, metric: str, hours: int = 24) -> list[tuple[int, float | None]]:
        """Return (ts, value) tuples for the last N hours for a given metric column."""
        col_map = {
            "load_1m": "load_1m", "load_5m": "load_5m", "load_15m": "load_15m",
            "cpu_pct": "cpu_pct", "cpu_temp": "cpu_temp",
            "mem_used_mb": "mem_used_mb", "mem_free_mb": "mem_free_mb",
            "swap_used_mb": "swap_used_mb", "disk_used_pct": "disk_used_pct",
        }
        col = col_map.get(metric)
        if not col:
            return []
        cutoff = int(time.time()) - hours * 3600
        with self._lock, self._conn() as c:
            rows = c.execute(
                f"SELECT ts, {col} FROM samples WHERE ts >= ? AND {col} IS NOT NULL ORDER BY ts",
                (cutoff,),
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def recent_alerts(self, limit: int = 50) -> list[dict]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def recent_notifications(self, limit: int = 50) -> list[dict]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT * FROM notification_log ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def apply_retention(self) -> tuple[int, int]:
        """Delete old data. Returns (raw_deleted, rollup_deleted)."""
        raw_cutoff = int(time.time()) - self.retention_days * 86400
        rollup_cutoff = int(time.time()) - self.rollup_retention_days * 86400
        with self._lock, self._conn() as c:
            r1 = c.execute("DELETE FROM samples WHERE ts < ?", (raw_cutoff,)).rowcount
            r2 = c.execute("DELETE FROM rollups_5m WHERE ts < ?", (rollup_cutoff,)).rowcount
            r3 = c.execute("DELETE FROM alerts WHERE ts < ?", (raw_cutoff,)).rowcount
            r4 = c.execute("DELETE FROM notification_log WHERE ts < ?", (raw_cutoff,)).rowcount
            c.execute("VACUUM")
        return r1 + r3 + r4, r2

    def stats(self) -> dict:
        with self._lock, self._conn() as c:
            return {
                "samples":       c.execute("SELECT COUNT(*) FROM samples").fetchone()[0],
                "rollups":       c.execute("SELECT COUNT(*) FROM rollups_5m").fetchone()[0],
                "alerts":        c.execute("SELECT COUNT(*) FROM alerts").fetchone()[0],
                "notifications": c.execute("SELECT COUNT(*) FROM notification_log").fetchone()[0],
                "oldest_sample_ts": c.execute("SELECT MIN(ts) FROM samples").fetchone()[0],
                "newest_sample_ts": c.execute("SELECT MAX(ts) FROM samples").fetchone()[0],
                "db_size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            }
