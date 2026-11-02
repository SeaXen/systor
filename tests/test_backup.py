"""Tests for backup creation, restoration, and archive security."""
import io
import tarfile

from systor.storage import Storage
from systor.services.backup_service import create_backup_archive, restore_backup_archive


def test_backup_create_and_restore(tmp_path):
    db_file = tmp_path / "systor.db"
    store = Storage(db_path=db_file)
    store.insert_sample({"ts": 1000, "cpu": {"percent": 12.5}})

    # Create backup archive
    archive, filename = create_backup_archive(store)
    assert len(archive) > 100
    assert filename.endswith(".tar.gz")

    # Restore into fresh database
    restore_db_file = tmp_path / "restored.db"
    restore_store = Storage(db_path=restore_db_file)

    ok, msg = restore_backup_archive(archive, restore_store)
    assert ok is True
    assert "restored successfully" in msg

    # Verify data exists in restored store
    stats = restore_store.stats()
    assert stats["samples"] >= 1


def test_backup_rejects_path_traversal(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        ti = tarfile.TarInfo(name="../evil.txt")
        data = b"malicious"
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))

    store = Storage(db_path=tmp_path / "test.db")
    ok, msg = restore_backup_archive(buf.getvalue(), store)
    assert ok is False
    assert "dangerous" in msg.lower() or "escapes" in msg.lower()


def test_backup_rejects_symlink_archive(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # Create a symlink member pointing outside
        ti = tarfile.TarInfo(name="symlink_escape")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "/etc/passwd"
        tar.addfile(ti)

    store = Storage(db_path=tmp_path / "test2.db")
    ok, msg = restore_backup_archive(buf.getvalue(), store)
    assert ok is False
