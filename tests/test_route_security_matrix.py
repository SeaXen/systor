import pytest
from systor.web import create_app
from systor.config import save_config, load_config


@pytest.fixture
def configured_app(tmp_path, monkeypatch):
    """Fixture providing an app configured with authentication."""
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))
    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setattr("systor.config.CONFIG_PATHS", [cfg_file])

    cfg = load_config()
    cfg["auth"] = {
        "enabled": True,
        "username": "admin",
        "password_hash": "scrypt:32768:8:1$test$test",
    }
    cfg["storage_page"] = {
        "enabled": True,
        "allowed_roots": [str(tmp_path)],
        "action_password_hash": "",
    }
    save_config(cfg)

    app = create_app(start_worker=False)
    app.config["TESTING"] = True
    return app


def test_public_wan_security_matrix(configured_app):
    """T02: Every registered route enforces public WAN perimeter (either allowed read-only or blocked with 401/403/404)."""
    client = configured_app.test_client()

    allowed_public_paths = {
        "/",
        "/health",
        "/offline",
        "/sw.js",
        "/api/version",
        "/api/snapshot",
        "/api/series",
        "/api/network-series",
        "/api/public-dashboard",
    }

    tested_rules = 0
    for rule in configured_app.url_map.iter_rules():
        # Skip static assets and routes with arguments like <path:filename>
        if "static" in rule.endpoint or "<" in rule.rule:
            continue

        tested_rules += 1
        path = rule.rule

        # Public WAN caller
        res = client.get(path, environ_base={"REMOTE_ADDR": "8.8.8.8"})

        if path in allowed_public_paths:
            # Must be 200 OK or 304 or 503 (stale health)
            assert res.status_code in (200, 304, 503), f"Public path {path} failed with {res.status_code}"
        else:
            # Everything else must be blocked: 401, 403, 404, or 405 (if method POST only)
            assert res.status_code in (401, 403, 404, 405), f"Private route {path} leaked to public WAN with {res.status_code}"

    assert tested_rules > 15, "Expected to test at least 15 distinct routes"

    # Verify /metrics security explicitly: 403 without token, 200 with configured token
    cfg = load_config()
    cfg["metrics"] = {"token": "secret-prom-token-123"}
    save_config(cfg)
    m_res_no_auth = client.get("/metrics", environ_base={"REMOTE_ADDR": "8.8.8.8"})
    assert m_res_no_auth.status_code == 403
    m_res_auth = client.get("/metrics", headers={"Authorization": "Bearer secret-prom-token-123"}, environ_base={"REMOTE_ADDR": "8.8.8.8"})
    assert m_res_auth.status_code == 200


def test_private_lan_authenticated_access_matrix(configured_app):
    """T02: Authenticated private LAN caller can access core pages without being blocked."""
    client = configured_app.test_client()
    with client.session_transaction() as sess:
        sess["systor_auth"] = True
        sess["systor_user"] = "admin"

    core_pages = [
        "/",
        "/apps",
        "/network",
        "/speed",
        "/logs",
        "/settings",
        "/storage",
    ]

    for page in core_pages:
        res = client.get(page, environ_base={"REMOTE_ADDR": "192.168.1.50"})
        assert res.status_code == 200, f"Core page {page} failed with {res.status_code}"
