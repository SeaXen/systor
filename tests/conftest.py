"""Pytest configuration and environment isolation fixtures."""
import pytest

from systor import storage, config
from systor.services import storage_service
from systor.blueprints import storage as bp_storage


@pytest.fixture(autouse=True)
def isolate_systor_environment(tmp_path, monkeypatch):
    """Ensure tests run against isolated paths in tmp_path."""
    test_db = tmp_path / "systor_isolated.db"
    test_cfg = tmp_path / "systor_isolated.yaml"
    test_ops = tmp_path / "storage_ops.jsonl"

    monkeypatch.setattr(storage, "DEFAULT_DB_PATH", test_db)
    monkeypatch.setattr(config, "CONFIG_PATHS", [test_cfg])
    monkeypatch.setattr(storage_service, "STORAGE_OP_LOG", test_ops)
    monkeypatch.setattr(bp_storage, "STORAGE_OP_LOG", test_ops)

    yield
