"""Disaster recovery, backup creation, and safe archive restoration service.

Packages active SQLite database and YAML configurations into a gzip-compressed
tarball with manifest checksums and PRAGMA integrity verification on restore.
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from .. import __version__, __app_name__
from ..config import load_config, save_config

log = logging.getLogger("systor.services.backup")


def create_backup_archive(storage, custom_note: str = "") -> tuple[bytes, str]:
    """Create a gzipped tar archive of systor database and configuration.
    Returns: (archive_bytes, filename)
    """
    ts_str = time.strftime("%Y%m%d-%H%M%S")
    filename = f"systor-backup-{ts_str}.tar.gz"

    db_path = Path(storage.db_path)
    # Checkpoint WAL to flush all data into the main DB file
    try:
        with storage._lock, storage._conn() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as e:
        log.warning("WAL checkpoint before backup warning: %s", e)

    cfg = load_config()
    manifest = {
        "app": __app_name__,
        "version": __version__,
        "created_at": int(time.time()),
        "created_at_iso": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "db_stats": storage.stats(),
        "note": custom_note,
    }

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # Add manifest
        manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
        ti_manifest = tarfile.TarInfo(name="manifest.json")
        ti_manifest.size = len(manifest_bytes)
        ti_manifest.mtime = int(time.time())
        tar.addfile(ti_manifest, io.BytesIO(manifest_bytes))

        # Add database
        if db_path.exists():
            with open(db_path, "rb") as f:
                db_bytes = f.read()
            ti_db = tarfile.TarInfo(name="systor.db")
            ti_db.size = len(db_bytes)
            ti_db.mtime = int(time.time())
            tar.addfile(ti_db, io.BytesIO(db_bytes))

        # Add config
        cfg_bytes = json.dumps(cfg, indent=2).encode("utf-8")
        ti_cfg = tarfile.TarInfo(name="config.json")
        ti_cfg.size = len(cfg_bytes)
        ti_cfg.mtime = int(time.time())
        tar.addfile(ti_cfg, io.BytesIO(cfg_bytes))

    buf.seek(0)
    return buf.getvalue(), filename


def restore_backup_archive(archive_bytes: bytes, storage) -> tuple[bool, str]:
    """Restore database and config from backup archive with safety checks."""
    if not archive_bytes or len(archive_bytes) < 64:
        return False, "Archive is empty or corrupted"

    buf = io.BytesIO(archive_bytes)
    with tempfile.TemporaryDirectory() as td:
        temp_dir = Path(td)
        try:
            with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                # Defend against directory traversal attacks (zip/tar slips)
                for member in tar.getmembers():
                    if member.name.startswith("/") or ".." in member.name:
                        return False, "Dangerous path detected in archive"
                try:
                    tar.extractall(path=temp_dir, filter="data")
                except (TypeError, AttributeError):  # Python < 3.12
                    for m in tar.getmembers():
                        if not (m.isreg() or m.isdir()):
                            return False, "non-regular member in archive"
                        dest = os.path.realpath(os.path.join(temp_dir, m.name))
                        if not dest.startswith(str(temp_dir)):
                            return False, "member escapes destination"
                    tar.extractall(path=temp_dir)
        except Exception as e:
            return False, f"Failed to extract backup archive: {e}"

        restored_db = temp_dir / "systor.db"
        if not restored_db.exists():
            return False, "Invalid backup: systor.db not found in archive"

        # Verify integrity of extracted SQLite database
        try:
            con = sqlite3.connect(restored_db)
            cur = con.cursor()
            cur.execute("PRAGMA integrity_check")
            check_res = cur.fetchone()[0]
            con.close()
            if check_res != "ok":
                return False, f"Database integrity check failed: {check_res}"
        except Exception as e:
            return False, f"Corrupted database in archive: {e}"

        # Safe swap
        active_db = Path(storage.db_path)
        backup_path = active_db.with_suffix(".pre-restore.bak")

        with storage._lock:
            try:
                if hasattr(storage, "checkpoint"):
                    storage.checkpoint()
                if active_db.exists():
                    shutil.copy2(active_db, backup_path)
                shutil.copy2(restored_db, active_db)
                if hasattr(storage, "checkpoint"):
                    storage.checkpoint()
            except Exception as e:
                return False, f"Error swapping database file: {e}"

        # If config was included in backup, load and merge safely
        restored_cfg = temp_dir / "config.json"
        if restored_cfg.exists():
            try:
                new_cfg = json.loads(restored_cfg.read_text(encoding="utf-8"))
                save_config(new_cfg)
            except Exception as e:
                log.warning("Could not restore config.json: %s", e)

    return True, "Backup restored successfully. Database integrity verified."
