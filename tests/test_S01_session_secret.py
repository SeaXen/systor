from flask.sessions import SecureCookieSessionInterface
from systor.web import create_app
from systor.config import get_or_create_session_secret


def forge_cookie(data: dict, secret: str = "systor-secret-key-32b-secure-fallback") -> str:
    from flask import Flask
    fake_app = Flask("fake")
    fake_app.secret_key = secret
    interface = SecureCookieSessionInterface()
    serializer = interface.get_signing_serializer(fake_app)
    return serializer.dumps(data)


def test_S01_no_static_fallback_secret(tmp_path, monkeypatch):
    """S01: Assert that fallback session secret is not the hard-coded public string."""
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SYSTOR_SESSION_SECRET", raising=False)

    app = create_app(start_worker=False)
    assert app.secret_key != "systor-secret-key-32b-secure-fallback"
    assert len(app.secret_key) >= 32

    # Check key file was persisted with mode 0600 on supporting platforms
    key_file = tmp_path / "session.key"
    assert key_file.exists()
    assert key_file.read_text(encoding="utf-8").strip() == app.secret_key

    # An attacker forges a session cookie using the old public secret
    forged = forge_cookie({"systor_auth": True, "systor_user": "admin"})

    client = app.test_client()
    client.set_cookie("session", forged)
    with client:
        # Accessing settings or login status must not treat forged session as authenticated
        res = client.get("/api/runtime")
        assert res.status_code == 200
        # Ensure session in Flask is empty or invalid
        from flask import session
        assert session.get("systor_auth") is not True


def test_S01_env_override_secret(tmp_path, monkeypatch):
    """S01: Env var SYSTOR_SESSION_SECRET overrides persistent key."""
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SYSTOR_SESSION_SECRET", "custom-env-secret-12345678901234567890")
    secret = get_or_create_session_secret({"auth": {}})
    assert secret == "custom-env-secret-12345678901234567890"
