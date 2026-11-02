"""Tests for system reliability, concurrency limits, and security enforcement."""
from werkzeug.security import generate_password_hash
from systor.terminal import authenticate_terminal_session, validate_terminal_token
from systor.web import create_app
from systor import speed


def test_emergency_terminal_auth_fail_when_unconfigured():
    """Verify emergency terminal refuses default passwords when password_hash is not set."""
    ok, msg, token = authenticate_terminal_session("systor", "127.0.0.1", "")
    assert ok is False
    assert token is None
    assert "Set auth.password_hash" in msg

    ok, msg, token = authenticate_terminal_session("admin", "127.0.0.1", "")
    assert ok is False
    assert token is None


def test_emergency_terminal_auth_success():
    """Verify emergency terminal issues tokens bound to IP when valid password hash is provided."""
    pw = "SuperSecureEmergency2026!"
    phash = generate_password_hash(pw)
    ok, msg, token = authenticate_terminal_session(pw, "10.0.0.5", phash)
    assert ok is True
    assert token is not None
    assert msg == "Authenticated"

    # Same IP succeeds
    assert validate_terminal_token(token, "10.0.0.5") is True
    # Different IP rejected
    assert validate_terminal_token(token, "10.0.0.6") is False


def test_speed_subprocess_semaphore():
    """Verify subprocess concurrency is guarded by bounded semaphore."""
    assert hasattr(speed, "_SUBPROCESS_SEMAPHORE")
    assert speed._SUBPROCESS_SEMAPHORE._initial_value == 2


def test_sse_stream_concurrency_limit():
    """Verify SSE streaming enforces a strict concurrency ceiling of 2."""
    from systor.blueprints import apps as bp_apps
    app = create_app(start_worker=False)
    client = app.test_client()

    with bp_apps._SSE_LOCK:
        bp_apps._SSE_CONCURRENT = 2

    try:
        resp = client.get("/api/apps/logs/stream?unit=systor.service", environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert resp.status_code == 429
        data = resp.get_json()
        assert "Too many concurrent" in data["error"]
    finally:
        with bp_apps._SSE_LOCK:
            bp_apps._SSE_CONCURRENT = 0


def test_csrf_double_submit_protection():
    """Verify non-GET requests require valid double-submit CSRF tokens."""
    app = create_app(start_worker=False)
    app.config["TESTING"] = True
    app.config["CSRF_CHECK_IN_TESTS"] = True
    client = app.test_client()

    # Mutation without CSRF token
    resp = client.post("/api/terminal/auth", json={"password": "test"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert resp.status_code == 403
    assert "CSRF" in resp.get_json()["error"]

    # Mutation with matching cookie and header
    token = "test_csrf_token_12345"
    client.set_cookie("systor_csrf", token)
    resp = client.post(
        "/api/terminal/auth",
        json={"password": "wrong_password"},
        headers={"X-CSRF-Token": token},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    # Passes CSRF gate (fails on bad password 401 instead of 403 CSRF)
    assert resp.status_code == 401
