from systor.web import create_app


def test_S03_csp_headers():
    """S03: Verify Content-Security-Policy headers are hardened."""
    app = create_app(start_worker=False)
    client = app.test_client()
    res = client.get("/")
    assert res.status_code == 200
    csp = res.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp
    assert "frame-ancestors 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "img-src 'self' data:" in csp

    # Check theme-boot.js is referenced
    html = res.get_data(as_text=True)
    assert 'theme-boot.js' in html
