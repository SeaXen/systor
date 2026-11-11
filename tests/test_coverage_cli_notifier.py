import sys
from unittest.mock import patch
from systor import cli
from systor import notifier


def test_cli_human_bytes():
    """Test CLI byte formatting helper."""
    assert cli._human_bytes(500) == "500.0 B"
    assert "KB" in cli._human_bytes(1024 * 5)
    assert "MB" in cli._human_bytes(1024 * 1024 * 15)
    assert "GB" in cli._human_bytes(1024 * 1024 * 1024 * 2)
    assert cli._human_bytes(None) == "?"


def test_cli_status(capsys):
    """Test `systor status` execution."""
    with patch.object(sys, "argv", ["systor", "status"]):
        cli.main()
    out = capsys.readouterr().out
    assert "Uptime" in out
    assert "CPU" in out


def test_cli_config_show(capsys):
    """Test `systor config show` execution."""
    with patch.object(sys, "argv", ["systor", "config", "show"]):
        cli.main()
    out = capsys.readouterr().out
    assert "collector" in out


def test_cli_passwd(tmp_path, monkeypatch, capsys):
    """Test `systor passwd` command sets password in config."""
    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_file])
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))

    with patch.object(sys, "argv", ["systor", "passwd", "--username", "cliadmin", "--password", "SuperSecurePassword2026!"]):
        cli.main()

    # Test password too short
    with patch.object(sys, "argv", ["systor", "passwd", "--password", "short"]):
        import pytest
        with pytest.raises(SystemExit):
            cli.main()
        assert "at least 12 characters" in capsys.readouterr().out

    # Test password prompt mismatch
    with patch.object(sys, "argv", ["systor", "passwd"]):
        with patch("getpass.getpass", side_effect=["Password123456", "MismatchedPass123"]):
            with pytest.raises(SystemExit):
                cli.main()
            assert "do not match" in capsys.readouterr().out


def test_cli_setup_channels(tmp_path, monkeypatch, capsys):
    """Test `systor setup telegram` and `systor setup discord`."""
    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_file])
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))

    with patch("systor.cli.send_telegram", return_value=(True, None)):
        # Setup Telegram
        with patch.object(sys, "argv", ["systor", "setup", "telegram", "--token", "123:ABC", "--chat-id", "987"]):
            cli.main()
        assert "Saved to" in capsys.readouterr().out

    with patch("systor.cli.send_discord", return_value=(True, None)):
        # Setup Discord
        with patch.object(sys, "argv", ["systor", "setup", "discord", "--webhook", "https://discord.com/webhook/test"]):
            cli.main()
        assert "Saved to" in capsys.readouterr().out


def test_notifier_error_branches():
    """Test notifier error handling when URLs fail or keys missing."""
    # Missing tokens
    ok, err = notifier.send_telegram("", "", "test")
    assert ok is False
    assert "missing" in err.lower()

    ok, err = notifier.send_discord("", "test")
    assert ok is False
    assert "missing" in err.lower()

    # Network error
    with patch("systor.notifier.safe_urlopen", side_effect=RuntimeError("DNS resolution failed")):
        ok, err = notifier.send_telegram("bad-token", "123", "test")
        assert ok is False
        assert "DNS" in err
