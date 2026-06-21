from pathlib import Path

from werkzeug.security import generate_password_hash

from systor import config as cfgmod
from systor import web as webmod
from systor.web import create_app


def _write_config(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _make_app(monkeypatch, tmp_path: Path, config_text: str):
    conf = tmp_path / "config.yaml"
    _write_config(conf, config_text)
    monkeypatch.setattr(cfgmod, "CONFIG_PATHS", [conf])
    webmod._login_rate_state.clear()
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


def test_login_rate_limit_blocks_after_repeated_failures(monkeypatch, tmp_path):
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
    for _ in range(4):
        r = client.post("/login", data={"username": "admin", "password": "wrong"})
        assert r.status_code == 401
    blocked = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert blocked.status_code == 429
    assert b"Too many failed logins" in blocked.data


def test_login_rate_limit_tracks_username_across_ips(monkeypatch, tmp_path):
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
  max_fails: 3
  cooldown_sec: 120
""",
    )
    client = app.test_client()
    r1 = client.post("/login", headers={"X-Forwarded-For": "10.0.0.1"}, data={"username": "admin", "password": "wrong"})
    r2 = client.post("/login", headers={"X-Forwarded-For": "10.0.0.2"}, data={"username": "admin", "password": "wrong"})
    r3 = client.post("/login", headers={"X-Forwarded-For": "10.0.0.3"}, data={"username": "admin", "password": "wrong"})
    assert r1.status_code == 401
    assert r2.status_code == 401
    assert r3.status_code == 429


def test_session_expires_after_idle_timeout(monkeypatch, tmp_path):
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
  idle_timeout_min: 1
""",
    )
    client = app.test_client()
    base = 1_700_000_000
    monkeypatch.setattr(webmod.time, "time", lambda: base)
    login = client.post("/login", data={"username": "admin", "password": "secret123"})
    assert login.status_code == 302
    monkeypatch.setattr(webmod.time, "time", lambda: base + 61)
    after = client.get("/settings", follow_redirects=False)
    assert after.status_code == 302
    assert "/login" in after.headers["Location"]
