"""Unit tests for the secure Web Terminal engine."""
from werkzeug.security import generate_password_hash

from systor.terminal import (
    authenticate_terminal_session,
    validate_terminal_token,
    execute_console_command,
)


def test_terminal_auth_success():
    hashed = generate_password_hash("secret123")
    ok, msg, token = authenticate_terminal_session("secret123", "192.168.1.100", hashed)
    assert ok is True
    assert token is not None
    assert len(token) > 20
    assert validate_terminal_token(token, "192.168.1.100") is True


def test_terminal_auth_wrong_password():
    hashed = generate_password_hash("secret123")
    ok, msg, token = authenticate_terminal_session("wrong", "192.168.1.100", hashed)
    assert ok is False
    assert token is None


def test_terminal_ip_mismatch():
    hashed = generate_password_hash("pass")
    ok, msg, token = authenticate_terminal_session("pass", "192.168.1.50", hashed)
    assert ok is True
    # Different IP should fail
    assert validate_terminal_token(token, "10.0.0.99") is False


def test_terminal_command_execution():
    hashed = generate_password_hash("admin")
    ok, msg, token = authenticate_terminal_session("admin", "127.0.0.1", hashed)
    assert ok is True

    res = execute_console_command(token, "127.0.0.1", "echo systor_console_test")
    assert res["ok"] is True
    assert "systor_console_test" in res["output"]


def test_terminal_command_invalid_token():
    res = execute_console_command("invalid_token", "127.0.0.1", "echo fail")
    assert res["ok"] is False
    assert "Invalid or expired" in res["error"]


def test_terminal_refuses_without_configured_hash():
    ok, msg, token = authenticate_terminal_session("admin", "127.0.0.1", "")
    assert ok is False
    assert token is None
    assert "Set auth.password_hash" in msg

    ok, msg, token = authenticate_terminal_session("systor", "127.0.0.1", "")
    assert ok is False
    assert token is None

