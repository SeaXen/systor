import os
from pathlib import Path
from systor.storage import Storage
from systor.services.backup_service import create_backup_archive, restore_backup_archive


def test_C01_atomic_same_filesystem_restore(tmp_path, monkeypatch):
    """C01: Verify that temporary extract directory is placed on same filesystem/parent as active_db."""
    real_replace = os.replace
    replaced_calls = []

    def mocked_replace(src, dst):
        src_p, dst_p = Path(src), Path(dst)
        # Simulate cross-device link failure if not in the same parent tree
        if src_p.parent.parent != dst_p.parent:
            raise OSError(18, "Invalid cross-device link")
        replaced_calls.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr("os.replace", mocked_replace)

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "systor.db"
    store = Storage(db_path=db_file)
    store.insert_sample({"ts": 1234, "cpu": {"percent": 50.0}})
    archive_bytes, _ = create_backup_archive(store)

    target_store = Storage(db_path=data_dir / "target.db")
    ok, msg = restore_backup_archive(archive_bytes, target_store)
    assert ok is True
    assert len(replaced_calls) == 1
    src, dst = replaced_calls[0]
    assert Path(src).parent.parent == Path(dst).parent
