import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from systor.storage import Storage


class TestDatabaseSchemaUpgrade(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "systor_legacy.db"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_legacy_schema_upgrade_preserves_data(self):
        """Simulate an older v1.0 database lacking modern columns and tables,

        and verify Storage auto-migrates without data loss.
        """
        now = int(time.time())
        # Step 1: Create a minimal legacy SQLite schema
        conn = sqlite3.connect(str(self.db_path))
        with conn:
            conn.execute("""
            CREATE TABLE samples (
                ts INTEGER NOT NULL,
                load_1m REAL,
                load_5m REAL,
                load_15m REAL,
                cpu_temp REAL,
                mem_total_mb INTEGER,
                mem_used_mb INTEGER,
                mem_free_mb INTEGER,
                mem_avail_mb INTEGER,
                swap_used_mb INTEGER,
                swap_total_mb INTEGER,
                disk_used_pct INTEGER,
                disk_used_gb REAL,
                disk_size_gb REAL,
                disk_mount TEXT,
                net_rx_bytes INTEGER,
                net_tx_bytes INTEGER,
                hostname TEXT
            );
            """)
            conn.execute("""
            CREATE TABLE alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                metric TEXT NOT NULL,
                severity TEXT NOT NULL,
                value REAL NOT NULL,
                threshold REAL NOT NULL,
                message TEXT NOT NULL,
                resolved_ts INTEGER
            );
            """)
            # Insert historical legacy rows
            conn.execute("""
            INSERT INTO samples (ts, load_1m, cpu_temp, mem_total_mb, mem_used_mb, hostname)
            VALUES (?, 1.25, 45.0, 16000, 4200, 'legacy-host');
            """, (now - 1800,))
            conn.execute("""
            INSERT INTO alerts (ts, metric, severity, value, threshold, message)
            VALUES (?, 'cpu_load_1m', 'alert', 5.5, 4.0, 'Cpu Load: 5.5 > 4.0');
            """, (now - 1800,))
        conn.close()

        # Step 2: Open with modern Storage instance (triggers _migrate)
        storage = Storage(self.db_path)

        # Step 3: Verify legacy data is preserved 100%
        stats = storage.stats()
        self.assertEqual(stats["samples"], 1)
        self.assertEqual(stats["alerts"], 1)

        # Step 4: Verify columns were safely added
        series = storage.series("load_1m", hours=24)
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0][1], 1.25)

        # Step 5: Verify new tables exist and can take writes
        tid = storage.add_uptime_target(
            name="Test Target",
            url="https://example.com/health",
            expected_status=200,
            interval_sec=60
        )
        self.assertIsInstance(tid, int)
        targets = storage.list_uptime_targets()
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["name"], "Test Target")


if __name__ == "__main__":
    unittest.main()
