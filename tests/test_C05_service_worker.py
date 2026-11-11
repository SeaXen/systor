from pathlib import Path
from systor import __version__
from systor.web import create_app


def test_C05_service_worker_headers_and_sync(tmp_path, monkeypatch):
    """C05: /sw.js is served at root scope with no-cache headers, synced cache name, and offline fallback."""
    monkeypatch.setenv("SYSTOR_DATA_DIR", str(tmp_path))
    app = create_app(start_worker=False)
    client = app.test_client()

    # /sw.js route
    resp = client.get("/sw.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers.get("Content-Type", "")
    assert "no-cache" in resp.headers.get("Cache-Control", "")
    assert resp.headers.get("Service-Worker-Allowed") == "/"

    # Cache name matches version
    sw_file = Path(__file__).resolve().parent.parent / "systor" / "static" / "sw.js"
    content = sw_file.read_text(encoding="utf-8")
    assert f"systor-shell-v{__version__}" in content
    assert "/offline" in content

    # Offline page exists and returns 200
    off_resp = client.get("/offline")
    assert off_resp.status_code == 200
    assert "Connection Unavailable" in off_resp.get_data(as_text=True)
