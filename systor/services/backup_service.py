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


ALLOWED_BACKUP_MEMBERS = {"manifest.json", "systor.db", "config.json"}


def create_backup_archive(storage, custom_note: str = "", include_secrets: bool = False) -> tuple[bytes, str]:
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
    safe_cfg = json.loads(json.dumps(cfg))
    if not include_secrets:
        # S04: Strip secrets by default
        if "telegram" in safe_cfg and isinstance(safe_cfg["telegram"], dict):
            safe_cfg["telegram"]["bot_token"] = ""
        if "discord" in safe_cfg and isinstance(safe_cfg["discord"], dict):
            safe_cfg["discord"]["webhook_url"] = ""
        if "auth" in safe_cfg and isinstance(safe_cfg["auth"], dict):
            safe_cfg["auth"]["password_hash"] = ""
            safe_cfg["auth"]["session_secret"] = ""
        if "storage_page" in safe_cfg and isinstance(safe_cfg["storage_page"], dict):
            safe_cfg["storage_page"]["action_password_hash"] = ""

    manifest = {
        "app": __app_name__,
        "version": __version__,
        "created_at": int(time.time()),
        "created_at_iso": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "db_stats": storage.stats(),
        "note": custom_note,
        "includes_secrets": bool(include_secrets),
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
        cfg_bytes = json.dumps(safe_cfg, indent=2).encode("utf-8")
        ti_cfg = tarfile.TarInfo(name="config.json")
        ti_cfg.size = len(cfg_bytes)
        ti_cfg.mtime = int(time.time())
        tar.addfile(ti_cfg, io.BytesIO(cfg_bytes))

    buf.seek(0)
    return buf.getvalue(), filename


def restore_backup_archive(archive_bytes: bytes, storage, restore_secrets: bool = False) -> tuple[bool, str]:
    """Restore database and config from backup archive with safety checks."""
    if not archive_bytes or len(archive_bytes) < 64:
        return False, "Archive is empty or corrupted"
    if len(archive_bytes) > 256 * 1024 * 1024:
        return False, "Archive exceeds maximum permitted size (256 MB)"

    active_db = Path(storage.db_path)
    restore_parent = active_db.parent
    try:
        restore_parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    buf = io.BytesIO(archive_bytes)
    # C01: Place temporary directory on same filesystem as active_db to ensure atomic same-device os.replace
    try:
        temp_dir_ctx = tempfile.TemporaryDirectory(dir=restore_parent, prefix=".restore-")
    except Exception:
        temp_dir_ctx = tempfile.TemporaryDirectory(prefix=".restore-")

    with temp_dir_ctx as td:
        temp_dir = Path(td)
        try:
            with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                members = tar.getmembers()
                # S05: Strictly validate all members
                for member in members:
                    if member.name.startswith("/") or ".." in member.name:
                        return False, f"Dangerous path detected in archive: {member.name}"
                    if not member.isreg():
                        return False, f"Dangerous non-regular member in archive: {member.name}"
                    base_name = os.path.basename(member.name)
                    if member.name != base_name or member.name not in ALLOWED_BACKUP_MEMBERS:
                        return False, f"Unexpected member in archive: {member.name}"
                    dest = os.path.realpath(os.path.join(temp_dir, member.name))
                    try:
                        if os.path.commonpath([dest, str(temp_dir)]) != str(temp_dir):
                            return False, f"Path traversal detected for member: {member.name}"
                    except ValueError:
                        return False, f"Cross-drive path traversal detected for member: {member.name}"

                try:
                    tar.extractall(path=temp_dir, filter="data")
                except (TypeError, AttributeError):  # Python < 3.12
                    for m in members:
                        tar.extract(m, path=temp_dir)
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

        # Check manifest for secrets flag
        manifest = {}
        manifest_file = temp_dir / "manifest.json"
        if manifest_file.exists():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Safe atomic swap
        backup_path = active_db.with_suffix(".pre-restore.bak")
        try:
            if hasattr(storage, "checkpoint"):
                storage.checkpoint()
            if active_db.exists():
                shutil.copy2(active_db, backup_path)
            os.replace(str(restored_db), str(active_db))
            for suffix in ("-wal", "-shm"):
                stale = active_db.with_name(active_db.name + suffix)
                try:
                    stale.unlink(missing_ok=True)
                except OSError:
                    pass
            if hasattr(storage, "checkpoint"):
                storage.checkpoint()
        except Exception as e:
            return False, f"Error swapping database file: {e}"

        # If config was included in backup, load and merge safely
        restored_cfg = temp_dir / "config.json"
        if restored_cfg.exists():
            try:
                new_cfg = json.loads(restored_cfg.read_text(encoding="utf-8"))
                current_cfg = load_config()
                # S04: Never overwrite auth credentials unless archive was created with secrets and admin confirmed
                archive_has_secrets = manifest.get("includes_secrets", False)
                if not (archive_has_secrets and restore_secrets):
                    # Preserve existing auth
                    if "auth" in current_cfg:
                        new_cfg["auth"] = current_cfg["auth"]
                    # Also preserve sensitive webhook tokens if new_cfg has empty strings
                    for sec, key in [("telegram", "bot_token"), ("discord", "webhook_url")]:
                        if sec in current_cfg and current_cfg[sec].get(key):
                            if not new_cfg.get(sec, {}).get(key):
                                new_cfg.setdefault(sec, {})[key] = current_cfg[sec][key]
                save_config(new_cfg)
            except Exception as e:
                log.warning("Could not restore config.json: %s", e)

    return True, "Backup restored successfully. Database integrity verified."
