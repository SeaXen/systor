from unittest.mock import MagicMock
from systor.services.systemd_service import (
    is_unit_allowed,
    restart_systemd_service,
)
from systor.config import save_config, load_config


def test_S06_restart_denied_for_unlisted_unit(tmp_path, monkeypatch):
    """S06: Restart is denied for any unit not matching systemd.allowed_units."""
    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_file])

    mock_storage = MagicMock()

    # Default allowed_units is ["systor-*.service"]
    assert is_unit_allowed("systor-collector.service") is True
    assert is_unit_allowed("cron.service") is False
    assert is_unit_allowed("nginx.service") is False
    assert is_unit_allowed("tailscaled.service") is False

    # Attempting to restart unlisted service is rejected
    ok, msg = restart_systemd_service("cron.service", user="admin", ip="127.0.0.1", storage=mock_storage)
    assert ok is False
    assert "not in systemd.allowed_units" in msg
    mock_storage.log_systemd_action.assert_called()


def test_S06_custom_allow_list(tmp_path, monkeypatch):
    """S06: Custom allowed units in configuration are respected."""
    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_file])

    cfg = load_config()
    cfg["systemd"] = {"allowed_units": ["custom-*.service", "worker.service"]}
    save_config(cfg)

    assert is_unit_allowed("custom-app.service") is True
    assert is_unit_allowed("worker.service") is True
    assert is_unit_allowed("systor-collector.service") is False
