import io
import tarfile
import pytest
from systor.storage import Storage
from systor.services.backup_service import restore_backup_archive


def test_S05_reject_hardlink(tmp_path):
    """S05: Restore rejects archives containing hardlink members."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        ti = tarfile.TarInfo(name="hardlink_test")
        ti.type = tarfile.LNKTYPE
        ti.linkname = "systor.db"
        tar.addfile(ti)

    store = Storage(db_path=tmp_path / "test.db")
    ok, msg = restore_backup_archive(buf.getvalue(), store)
    assert ok is False
    assert "dangerous" in msg.lower() or "unexpected" in msg.lower()


def test_S05_reject_symlink(tmp_path):
    """S05: Restore rejects archives containing symlink members."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        ti = tarfile.TarInfo(name="symlink_test")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "/etc/shadow"
        tar.addfile(ti)

    store = Storage(db_path=tmp_path / "test.db")
    ok, msg = restore_backup_archive(buf.getvalue(), store)
    assert ok is False


def test_S05_reject_unexpected_extra_file(tmp_path):
    """S05: Restore rejects archives containing unexpected files (e.g. evil.py)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        ti = tarfile.TarInfo(name="evil.py")
        content = b"print('hacked')"
        ti.size = len(content)
        tar.addfile(ti, io.BytesIO(content))

    store = Storage(db_path=tmp_path / "test.db")
    ok, msg = restore_backup_archive(buf.getvalue(), store)
    assert ok is False
    assert "unexpected" in msg.lower()


def test_S05_reject_oversized_archive(tmp_path):
    """S05: Restore rejects archive exceeding 256 MB."""
    store = Storage(db_path=tmp_path / "test.db")
    dummy_huge = b"0" * (256 * 1024 * 1024 + 10)
    ok, msg = restore_backup_archive(dummy_huge, store)
    assert ok is False
    assert "maximum" in msg.lower() or "exceeds" in msg.lower()
