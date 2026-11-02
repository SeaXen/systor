"""Unit tests for the two-way interactive Telegram Bot."""
from unittest.mock import MagicMock, patch

from systor.bot import (
    handle_bot_command,
    send_telegram_message,
    start_bot_daemon,
)


def test_handle_bot_help():
    msg = handle_bot_command("/help")
    assert "/status" in msg
    assert "/speed" in msg
    assert "/alerts" in msg


def test_handle_bot_ping():
    msg = handle_bot_command("/ping")
    assert "Pong" in msg


def test_handle_bot_status():
    with patch("systor.bot.collect_snapshot") as mock_snap:
        mock_snap.return_value = {
            "hostname": "test-box",
            "uptime_sec": 7200,
            "cpu": {"percent": 15.0, "load_1m": 0.5, "load_5m": 0.3, "temp_c": 45.0},
            "memory": {"used_mb": 2048, "total_mb": 8192},
            "disks": [{"mount": "/", "used_pct": 42.0}],
        }
        msg = handle_bot_command("/status")
        assert "test-box" in msg
        assert "15.0%" in msg
        assert "2h 0m" in msg
        assert "25.0%" in msg  # 2048 / 8192


def test_handle_bot_alerts():
    mock_storage = MagicMock()
    mock_storage.recent_alerts.return_value = [
        {"ts": 1700000000, "metric": "cpu_load_1m", "message": "High CPU load (4.5 > 4.0)"}
    ]
    msg = handle_bot_command("/alerts", storage=mock_storage)
    assert "High CPU load" in msg
    assert "cpu_load_1m" in msg


def test_send_telegram_message():
    with patch("urllib.request.urlopen") as mock_url:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_url.return_value = mock_resp

        ok = send_telegram_message("test_token", "12345", "Hello World")
        assert ok is True


def test_start_bot_daemon_disabled():
    cfg = {"telegram": {"enabled": False}}
    th = start_bot_daemon(cfg)
    assert th is None
