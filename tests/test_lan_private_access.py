"""LAN clients see the full app when auth is disabled; public clients do not."""
from systor.web import create_app


def _client():
    app = create_app(start_worker=False)
    app.config["TESTING"] = True
    return app.test_client()


def test_lan_client_gets_full_ui_without_auth():
    c = _client()
    hdr = {"X-Forwarded-For": "192.168.0.50"}
    body = c.get("/", headers=hdr).get_data(as_text=True)
    assert ">Apps<" in body
    assert ">Speed<" in body
    assert ">Settings<" in body
    assert c.get("/apps", headers=hdr).status_code == 200
    assert c.get("/api/top-processes?by=cpu&n=5", headers=hdr).status_code == 200


def test_tailscale_client_gets_full_ui():
    c = _client()
    body = c.get("/", headers={"X-Forwarded-For": "100.88.0.2"}).get_data(as_text=True)
    assert ">Apps<" in body


def test_public_client_stays_limited():
    c = _client()
    hdr = {"CF-Connecting-IP": "8.8.8.8"}
    body = c.get("/", headers=hdr).get_data(as_text=True)
    assert ">Apps<" not in body
    assert c.get("/apps", headers=hdr).status_code == 404
    assert c.get("/api/top-processes?by=cpu&n=5", headers=hdr).status_code == 403
