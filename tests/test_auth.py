from pathlib import Path

from werkzeug.security import generate_password_hash

from systor import config as cfgmod
from systor.web import create_app


def _write_config(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _make_app(monkeypatch, tmp_path: Path, config_text: str):
    conf = tmp_path / "config.yaml"
    _write_config(conf, config_text)
    monkeypatch.setattr(cfgmod, "CONFIG_PATHS", [conf])
    app = create_app()
    app.config.update(TESTING=True)
    return app


def test_load_config_has_auth_defaults():
    cfg = cfgmod.load_config()
    assert "auth" in cfg
    assert cfg["auth"]["enabled"] is False
    assert cfg["auth"]["mode"] == "admin_only"
    assert cfg["auth"]["username"] == "admin"


def test_private_settings_requires_login_when_auth_enabled(monkeypatch, tmp_path):
    password_hash = generate_password_hash("secret123")
    app = _make_app(
        monkeypatch,
        tmp_path,
        f"""
web:
  host: "0.0.0.0"
  port: 6677
auth:
  enabled: true
  mode: "admin_only"
  username: "admin"
  password_hash: "{password_hash}"
  session_secret: "test-secret"
""",
    )
    client = app.test_client()
    resp = client.get("/settings")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_login_allows_private_settings(monkeypatch, tmp_path):
    password_hash = generate_password_hash("secret123")
    app = _make_app(
        monkeypatch,
        tmp_path,
        f"""
web:
  host: "0.0.0.0"
  port: 6677
auth:
  enabled: true
  mode: "admin_only"
  username: "admin"
  password_hash: "{password_hash}"
  session_secret: "test-secret"
""",
    )
    client = app.test_client()
    login = client.post(
        "/login",
        data={"username": "admin", "password": "secret123"},
        follow_redirects=False,
    )
    assert login.status_code == 302
    after = client.get("/settings")
    assert after.status_code == 200
    assert b"Settings" in after.data


def test_public_dashboard_stays_open_when_auth_enabled(monkeypatch, tmp_path):
    password_hash = generate_password_hash("secret123")
    app = _make_app(
        monkeypatch,
        tmp_path,
        f"""
web:
  host: "0.0.0.0"
  port: 6677
auth:
  enabled: true
  mode: "admin_only"
  username: "admin"
  password_hash: "{password_hash}"
  session_secret: "test-secret"
""",
    )
    client = app.test_client()
    pub = {"X-Forwarded-For": "8.8.8.8"}
    dashboard = client.get("/", headers=pub)
    assert dashboard.status_code == 200
    blocked = client.get("/settings", headers=pub)
    assert blocked.status_code == 404
