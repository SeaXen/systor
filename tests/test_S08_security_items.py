import pytest
from unittest.mock import MagicMock
from systor.security import safe_urlopen
from systor.storage import Storage
from systor.web import create_app
from systor.terminal import execute_console_command, authenticate_terminal_session
from systor.config import load_config, save_config


def test_S08_safe_urlopen_scheme_check():
    """S08: safe_urlopen allows http/https and blocks dangerous schemes like file:// or custom."""
    with pytest.raises(ValueError, match="only http and https"):
        safe_urlopen("file:///etc/passwd")

    with pytest.raises(ValueError, match="only http and https"):
        safe_urlopen("gopher://evil.com")


def test_S08_storage_sql_safety(tmp_path):
    """S08: Storage series and speedtest queries safely ignore invalid or unknown column inputs."""
    db_file = tmp_path / "test.db"
    store = Storage(db_path=db_file)

    # Unknown metric name is safely rejected by returning empty list
    rows = store.series("non_existent_metric'; DROP TABLE samples;--", hours=1)
    assert rows == []

    # Speedtest search with strange parameters
    results, total = store.recent_speedtests(limit=50, provider="' OR 1=1;--", run_type="all")
    assert isinstance(results, list)
    assert total == 0


def test_S08_health_masks_public_errors(tmp_path, monkeypatch):
    """S08: /health masks detailed collector error messages for non-private callers."""
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))
    from systor.blueprints.common import get_storage
    import time
    app = create_app(start_worker=False)
    client = app.test_client()

    with app.app_context():
        store = get_storage()
        # Record a fresh sample so health check is fresh
        store.insert_sample({"cpu_pct": 12.5, "timestamp": time.time()})

    # Public client hitting /health
    res = client.get("/health", environ_base={"REMOTE_ADDR": "8.8.8.8"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True

    # When stale data, public error detail is masked to just status: stale
    with app.app_context():
        # Record old sample
        orig_stats = store.stats()
        orig_stats["newest_sample_ts"] = time.time() - 100
        monkeypatch.setattr(store, "stats", lambda: orig_stats)
        res_stale = client.get("/health", environ_base={"REMOTE_ADDR": "8.8.8.8"})
        assert res_stale.status_code == 503
        data_stale = res_stale.get_json()
        assert data_stale["status"] == "stale"
        assert "error" not in data_stale

    # When exception raised in storage, public error is masked to generic text
    def fail_stats():
        raise RuntimeError("Sensitive sqlite internal detail /var/lib/...")

    monkeypatch.setattr("systor.blueprints.dashboard.get_storage", lambda: MagicMock(stats=fail_stats))
    res_fail = client.get("/health", environ_base={"REMOTE_ADDR": "8.8.8.8"})
    assert res_fail.status_code == 503
    data_fail = res_fail.get_json()
    assert data_fail["error"] == "Database unreachable"
    assert "Sensitive" not in data_fail["error"]


def test_S08_terminal_enabled_guard(tmp_path, monkeypatch):
    """S08: Terminal command execution is rejected when terminal.enabled is false."""
    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_file])

    cfg = load_config()
    cfg["terminal"] = {"enabled": False}
    save_config(cfg)

    ok, msg, token = authenticate_terminal_session("admin", "127.0.0.1", "any-hash")
    assert ok is False
    assert "disabled" in msg.lower()

    res = execute_console_command("fake-token", "127.0.0.1", "echo hi")
    assert res["ok"] is False
    assert "disabled" in res["error"].lower()
