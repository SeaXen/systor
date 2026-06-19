"""SQLite storage layer with retention and rollup."""
from __future__ import annotations
import sqlite3
import time
import json
import threading
import datetime as dt
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
    disk_read_mbps  REAL,
    disk_write_mbps REAL,
    net_rx_bytes    INTEGER,
    net_tx_bytes    INTEGER,
    hostname        TEXT
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);

CREATE TABLE IF NOT EXISTS iface_samples (
    ts INTEGER NOT NULL,
    iface TEXT NOT NULL,
    rx_bytes INTEGER,
    tx_bytes INTEGER
);
CREATE INDEX IF NOT EXISTS idx_iface_samples_ts ON iface_samples(ts);
CREATE INDEX IF NOT EXISTS idx_iface_samples_iface_ts ON iface_samples(iface, ts);

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

CREATE TABLE IF NOT EXISTS speedtests (
    ts INTEGER NOT NULL,
    provider TEXT NOT NULL,
    target TEXT,
    mode TEXT NOT NULL,
    server_id TEXT,
    run_type TEXT NOT NULL DEFAULT 'manual',
    ping_ms REAL,
    jitter_ms REAL,
    dl_mbps REAL,
    ul_mbps REAL,
    note TEXT,
    ok INTEGER NOT NULL DEFAULT 1,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_speedtests_ts ON speedtests(ts);
CREATE INDEX IF NOT EXISTS idx_speedtests_provider_ts ON speedtests(provider, ts);
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
            self._migrate(c)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")

    def _migrate(self, c: sqlite3.Connection) -> None:
        """Apply lightweight additive migrations for existing SQLite DBs."""
        existing = {row[1] for row in c.execute("PRAGMA table_info(samples)").fetchall()}
        additions = {
            "cpu_pct": "REAL",
            "disk_read_mbps": "REAL",
            "disk_write_mbps": "REAL",
        }
        for col, typ in additions.items():
            if col not in existing:
                c.execute(f"ALTER TABLE samples ADD COLUMN {col} {typ}")
        speed_existing = {row[1] for row in c.execute("PRAGMA table_info(speedtests)").fetchall()}
        speed_additions = {
            "server_id": "TEXT",
            "run_type": "TEXT NOT NULL DEFAULT 'manual'",
            "jitter_ms": "REAL",
        }
        for col, typ in speed_additions.items():
            if col not in speed_existing:
                c.execute(f"ALTER TABLE speedtests ADD COLUMN {col} {typ}")

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
            disk_io = s.get("disk_io", {}) or {}
            ts = int(s.get("ts", int(time.time())))
            c.execute(
                """INSERT INTO samples
                (ts, load_1m, load_5m, load_15m, cpu_pct, cpu_temp,
                 mem_total_mb, mem_used_mb, mem_free_mb, mem_avail_mb,
                 swap_used_mb, swap_total_mb,
                 disk_used_pct, disk_used_gb, disk_size_gb, disk_mount,
                 disk_read_mbps, disk_write_mbps,
                 net_rx_bytes, net_tx_bytes, hostname)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ts,
                    cpu.get("load_1m"), cpu.get("load_5m"), cpu.get("load_15m"),
                    cpu.get("percent"), cpu.get("temp_c"),
                    mem.get("total_mb"), mem.get("used_mb"), mem.get("free_mb"), mem.get("available_mb"),
                    mem.get("swap_used_mb"), mem.get("swap_total_mb"),
                    worst_disk.get("used_pct"), worst_disk.get("used_gb"), worst_disk.get("size_gb"), worst_disk.get("mount"),
                    disk_io.get("read_mbps"), disk_io.get("write_mbps"),
                    net.get("rx_bytes"), net.get("tx_bytes"),
                    s.get("hostname"),
                ),
            )
            iface_rows = s.get("network_interfaces") or []
            if iface_rows:
                c.executemany(
                    "INSERT INTO iface_samples (ts, iface, rx_bytes, tx_bytes) VALUES (?,?,?,?)",
                    [(ts, str(r.get("iface") or "?"), int(r.get("rx_bytes") or 0), int(r.get("tx_bytes") or 0)) for r in iface_rows],
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
            "disk_read_mbps": "disk_read_mbps", "disk_write_mbps": "disk_write_mbps",
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

    def log_speedtest(self, row: dict) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO speedtests (ts, provider, target, mode, server_id, run_type, ping_ms, jitter_ms, dl_mbps, ul_mbps, note, ok, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    int(row.get("ts") or time.time()),
                    str(row.get("provider") or "ookla"),
                    str(row.get("target") or ""),
                    str(row.get("mode") or "wan"),
                    str(row.get("server_id") or ""),
                    str(row.get("run_type") or "manual"),
                    row.get("ping_ms"),
                    row.get("jitter_ms"),
                    row.get("dl_mbps"),
                    row.get("ul_mbps"),
                    str(row.get("note") or ""),
                    1 if row.get("ok", True) else 0,
                    str(row.get("raw_json") or ""),
                ),
            )

    def recent_speedtests(self, limit: int = 50, provider: str = "all", run_type: str = "all", offset: int = 0) -> tuple[list[dict], int]:
        where = []
        vals = []
        if provider and provider != "all":
            where.append("provider = ?")
            vals.append(provider)
        if run_type and run_type != "all":
            where.append("COALESCE(run_type, 'manual') = ?")
            vals.append(run_type)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with self._lock, self._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM speedtests" + clause, tuple(vals)).fetchone()[0]
            rows = c.execute(
                "SELECT * FROM speedtests" + clause + " ORDER BY ts DESC LIMIT ? OFFSET ?",
                tuple(vals + [limit, offset]),
            ).fetchall()
        return [dict(r) for r in rows], int(total)

    def network_series(self, hours: int = 24) -> list[dict]:
        """Return rx/tx throughput points derived from cumulative net byte counters."""
        cutoff = int(time.time()) - hours * 3600
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT ts, net_rx_bytes, net_tx_bytes FROM samples WHERE ts >= ? AND net_rx_bytes IS NOT NULL AND net_tx_bytes IS NOT NULL ORDER BY ts",
                (cutoff,),
            ).fetchall()
        out: list[dict] = []
        prev = None
        for r in rows:
            cur = {"ts": int(r[0]), "rx": int(r[1] or 0), "tx": int(r[2] or 0)}
            if prev is not None:
                dt_sec = cur["ts"] - prev["ts"]
                if dt_sec > 0:
                    d_rx = cur["rx"] - prev["rx"]
                    d_tx = cur["tx"] - prev["tx"]
                    if d_rx < 0:
                        d_rx = 0
                    if d_tx < 0:
                        d_tx = 0
                    out.append({
                        "ts": cur["ts"],
                        "rx_mbps": round(d_rx / dt_sec / (1024 * 1024), 4),
                        "tx_mbps": round(d_tx / dt_sec / (1024 * 1024), 4),
                    })
            prev = cur
        return out

    def network_usage_buckets(self, granularity: str = "day", limit: int = 30, iface: str | None = None) -> list[dict]:
        """Aggregate network usage deltas into day/week/month/year buckets. If iface is set, uses iface_samples history."""
        if granularity not in {"day", "week", "month", "year"}:
            granularity = "day"
        with self._lock, self._conn() as c:
            if iface and iface != "all":
                rows = c.execute(
                    "SELECT ts, rx_bytes, tx_bytes FROM iface_samples WHERE iface = ? ORDER BY ts",
                    (iface,),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT ts, net_rx_bytes, net_tx_bytes FROM samples WHERE net_rx_bytes IS NOT NULL AND net_tx_bytes IS NOT NULL ORDER BY ts"
                ).fetchall()
        buckets: dict[str, dict] = {}
        prev = None
        for r in rows:
            cur = {"ts": int(r[0]), "rx": int(r[1] or 0), "tx": int(r[2] or 0)}
            if prev is not None and cur["ts"] > prev["ts"]:
                d_rx = cur["rx"] - prev["rx"]
                d_tx = cur["tx"] - prev["tx"]
                if d_rx < 0:
                    d_rx = 0
                if d_tx < 0:
                    d_tx = 0
                stamp = dt.datetime.fromtimestamp(cur["ts"])
                if granularity == "week":
                    iso = stamp.isocalendar()
                    key = f"{iso.year}-W{iso.week:02d}"
                elif granularity == "month":
                    key = stamp.strftime("%Y-%m")
                elif granularity == "year":
                    key = stamp.strftime("%Y")
                else:
                    key = stamp.strftime("%Y-%m-%d")
                bucket = buckets.setdefault(key, {"label": key, "rx_bytes": 0, "tx_bytes": 0, "samples": 0, "start_ts": cur["ts"], "end_ts": cur["ts"]})
                bucket["rx_bytes"] += d_rx
                bucket["tx_bytes"] += d_tx
                bucket["samples"] += 1
                if cur["ts"] < bucket["start_ts"]:
                    bucket["start_ts"] = cur["ts"]
                bucket["end_ts"] = cur["ts"]
            prev = cur
        result = list(buckets.values())[-max(1, limit):]
        for row in result:
            row["total_bytes"] = row["rx_bytes"] + row["tx_bytes"]
            row["rx_gb"] = round(row["rx_bytes"] / (1024 ** 3), 3)
            row["tx_gb"] = round(row["tx_bytes"] / (1024 ** 3), 3)
            row["total_gb"] = round(row["total_bytes"] / (1024 ** 3), 3)
            duration = max(1, int(row.get("end_ts", 0)) - int(row.get("start_ts", 0)))
            row["avg_rate_mbps"] = round(row["total_bytes"] / duration / (1024 * 1024), 4)
        return result

    def apply_retention(self) -> tuple[int, int]:
        """Delete old data. Returns (raw_deleted, rollup_deleted)."""
        raw_cutoff = int(time.time()) - self.retention_days * 86400
        rollup_cutoff = int(time.time()) - self.rollup_retention_days * 86400
        with self._lock, self._conn() as c:
            r1 = c.execute("DELETE FROM samples WHERE ts < ?", (raw_cutoff,)).rowcount
            r2 = c.execute("DELETE FROM rollups_5m WHERE ts < ?", (rollup_cutoff,)).rowcount
            r3 = c.execute("DELETE FROM alerts WHERE ts < ?", (raw_cutoff,)).rowcount
            r4 = c.execute("DELETE FROM notification_log WHERE ts < ?", (raw_cutoff,)).rowcount
            r5 = c.execute("DELETE FROM speedtests WHERE ts < ?", (raw_cutoff,)).rowcount
            c.execute("VACUUM")
        return r1 + r3 + r4 + r5, r2

    def stats(self) -> dict:
        with self._lock, self._conn() as c:
            return {
                "samples":       c.execute("SELECT COUNT(*) FROM samples").fetchone()[0],
                "rollups":       c.execute("SELECT COUNT(*) FROM rollups_5m").fetchone()[0],
                "alerts":        c.execute("SELECT COUNT(*) FROM alerts").fetchone()[0],
                "notifications": c.execute("SELECT COUNT(*) FROM notification_log").fetchone()[0],
                "speedtests":    c.execute("SELECT COUNT(*) FROM speedtests").fetchone()[0],
                "oldest_sample_ts": c.execute("SELECT MIN(ts) FROM samples").fetchone()[0],
                "newest_sample_ts": c.execute("SELECT MAX(ts) FROM samples").fetchone()[0],
                "db_size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            }
