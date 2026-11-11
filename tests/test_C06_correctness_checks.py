from unittest.mock import patch
from systor.web import create_app
from systor.storage import Storage
from systor.config import save_config, load_config
from systor.services.backup_service import create_backup_archive, restore_backup_archive


def test_C06_config_restore_preserves_auth(tmp_path, monkeypatch):
    """C06: Restoring a backup archive without secrets never wipes existing auth configuration."""
    db_file = tmp_path / "systor.db"
    store = Storage(str(db_file))
    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_file])
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))

    # Existing config has strong auth
    initial_cfg = load_config()
    initial_cfg["auth"] = {
        "enabled": True,
        "username": "superadmin",
        "password_hash": "scrypt:32768:8:1$existinghash$secure",
    }
    save_config(initial_cfg)

    # Create backup without secrets
    archive_bytes, _ = create_backup_archive(store, include_secrets=False)

    # Even if someone calls restore with restore_secrets=False (default)
    ok, msg = restore_backup_archive(archive_bytes, store, restore_secrets=False)
    assert ok is True

    # Current config must retain original credentials
    reloaded_cfg = load_config()
    assert reloaded_cfg["auth"]["username"] == "superadmin"
    assert reloaded_cfg["auth"]["password_hash"] == "scrypt:32768:8:1$existinghash$secure"


def test_C06_restart_collector_rate_limit(tmp_path, monkeypatch):
    """C06: Collector restart endpoint enforces cooldown to prevent rapid process churn."""
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))
    app = create_app(start_worker=False)
    app.config["TESTING"] = True
    client = app.test_client()

    with patch("systor.blueprints.settings.find_collector_pid", return_value=None):
        with patch("subprocess.Popen"):
            res1 = client.post("/api/restart-collector", environ_base={"REMOTE_ADDR": "127.0.0.1"})
            # First attempt triggers restart
            assert res1.status_code == 200

            # Immediate second attempt rejected by rate limiter
            res2 = client.post("/api/restart-collector", environ_base={"REMOTE_ADDR": "127.0.0.1"})
            assert res2.status_code == 429
            data = res2.get_json()
            assert "recently requested" in data["error"]


def test_C06_uptime_summary_empty_state(tmp_path):
    """C06: Uptime summary handles brand-new targets with 0 recorded checks cleanly."""
    db_file = tmp_path / "test_uptime.db"
    store = Storage(str(db_file))

    tid = store.add_uptime_target("New Target", "https://example.com")
    assert tid > 0

    summary = store.get_uptime_summary(hours=24)
    assert len(summary) == 1
    t = summary[0]
    assert t["name"] == "New Target"
    assert t["total_checks"] == 0
    assert t["last_check"] is None
