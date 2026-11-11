import pytest
from systor.web import create_app


@pytest.fixture
def csrf_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_path])

    app = create_app(start_worker=False)
    app.config["TESTING"] = True
    app.config["CSRF_CHECK_IN_TESTS"] = True
    return app.test_client()


def test_S07_csrf_session_only_validation(csrf_client):
    """S07: CSRF double-submit requires valid token matching session (cookies alone do not authenticate)."""
    # 1. Establish session
    res = csrf_client.get("/")
    assert res.status_code == 200

    from flask import session
    with csrf_client:
        csrf_client.get("/")
        csrf_token = session.get("systor_csrf")
    assert csrf_token is not None

    mutating_routes = [
        ("/api/logs/clear", {}),
        ("/api/uptime/check-now", {}),
        ("/api/speed/live/stop", {}),
        ("/api/apply", {}),
        ("/api/storage/ops/clear", {"action_password": ""}),
    ]

    for path, payload in mutating_routes:
        # A. Missing token -> 403
        r_missing = csrf_client.post(path, json=payload)
        assert r_missing.status_code == 403, f"Expected 403 on {path} without CSRF token"
        assert "Invalid or missing CSRF token" in r_missing.get_json()["error"]

        # B. Wrong token -> 403
        r_wrong = csrf_client.post(path, json=payload, headers={"X-CSRF-Token": "bad-csrf-token-12345"})
        assert r_wrong.status_code == 403, f"Expected 403 on {path} with wrong CSRF token"

        # C. Correct token -> Success (200)
        r_ok = csrf_client.post(path, json=payload, headers={"X-CSRF-Token": csrf_token})
        assert r_ok.status_code == 200, f"Expected 200 on {path} with valid CSRF token, got {r_ok.status_code}"


def test_S07_cookie_secure_on_https(tmp_path, monkeypatch):
    """S07: SESSION_COOKIE_SECURE is set when request is HTTPS."""
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))
    app = create_app(start_worker=False)
    client = app.test_client()

    # Request over https forwarded proto
    res = client.get("/", headers={"X-Forwarded-Proto": "https"})
    assert res.status_code == 200
    assert app.config.get("SESSION_COOKIE_SECURE") is True
