import urllib.error
from unittest.mock import patch, MagicMock

from systor.services.ssl_service import probe_remote_cert, get_ssl_certificates
from systor.services.uptime_service import ping_target, run_single_audit_cycle, _fail_count, _in_alert
from systor.storage import Storage


def test_ssl_service_deep(tmp_path):
    """Test remote TLS probing with cert details and local letsencrypt scanning."""
    # Mock TLS socket handshake
    mock_cert = {
        "notAfter": "May 20 12:00:00 2028 GMT",
        "issuer": ((("organizationName", "Let's Encrypt"),),),
        "subject": ((("commonName", "systor.local"),),),
        "subjectAltName": (("DNS", "systor.local"), ("DNS", "www.systor.local")),
    }

    mock_ssock = MagicMock()
    mock_ssock.getpeercert.return_value = mock_cert

    mock_ctx = MagicMock()
    mock_ctx.wrap_socket.return_value.__enter__.return_value = mock_ssock

    with patch("ssl.create_default_context", return_value=mock_ctx):
        with patch("socket.create_connection") as mock_conn:
            mock_conn.return_value.__enter__.return_value = MagicMock()
            cert_res = probe_remote_cert("systor.local", port=443)
            assert cert_res is not None
            assert cert_res["host"] == "systor.local"
            assert cert_res["issuer"] == "Let's Encrypt"
            assert cert_res["status"] == "ok"
            assert cert_res["valid"] is True
            assert "systor.local" in cert_res["sans"]

    # scan_local_letsencrypt
    le_dir = tmp_path / "letsencrypt" / "live" / "my.domain.com"
    le_dir.mkdir(parents=True)
    (le_dir / "cert.pem").write_text("CERT")

    with patch("systor.services.ssl_service.Path", return_value=tmp_path / "letsencrypt" / "live"):
        with patch("systor.services.ssl_service.probe_remote_cert") as mock_probe:
            mock_probe.return_value = {
                "host": "my.domain.com",
                "days_left": 80,
                "status": "ok",
                "valid": True,
            }
            certs = get_ssl_certificates()
            assert isinstance(certs, list)


def test_uptime_service_deep(tmp_path):
    """Test TCP ping, verify_tls=False, and debounced alert/recover cycles."""
    # 1. TCP ping success
    with patch("socket.create_connection") as mock_conn:
        mock_conn.return_value.__enter__.return_value = MagicMock()
        ok, code, lat, err = ping_target({"url": "tcp://127.0.0.1:8080"})
        assert ok is True
        assert code == 200

    # TCP ping failure
    with patch("socket.create_connection", side_effect=OSError("connection refused")):
        ok, code, lat, err = ping_target({"url": "tcp://127.0.0.1:9999"})
        assert ok is False
        assert "refused" in err

    # 2. HTTP with verify_tls=False
    with patch("systor.services.uptime_service.safe_urlopen") as mock_url:
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_url.return_value.__enter__.return_value = mock_resp
        ok, code, lat, err = ping_target({"url": "https://self-signed.local", "verify_tls": False})
        assert ok is True
        assert code == 200

    # 3. HTTP with URLError
    with patch("systor.services.uptime_service.safe_urlopen", side_effect=urllib.error.URLError("DNS lookup failed")):
        ok, code, lat, err = ping_target({"url": "http://nonexistent.local"})
        assert ok is False
        assert "DNS" in err

    # HTTP with general Exception
    with patch("systor.services.uptime_service.safe_urlopen", side_effect=RuntimeError("Socket error")):
        ok, code, lat, err = ping_target({"url": "http://runtime-err.local"})
        assert ok is False
        assert "Socket" in err

    # 4. Debounced alert cycle (2 failures -> alert, 1 success -> recover)
    db_file = tmp_path / "uptime_alerts.db"
    store = Storage(str(db_file))
    store.add_uptime_target(name="Critical Service", url="http://critical.local", interval_sec=10)

    _fail_count.clear()
    _in_alert.clear()
    from systor.services.uptime_service import _last_check_ts
    _last_check_ts.clear()

    # Fail 1: no alert yet
    with patch("systor.services.uptime_service.ping_target", return_value=(False, 500, 20.0, "Internal Server Error")):
        run_single_audit_cycle(store)
        assert len(store.recent_alerts(limit=5)) == 0

    # Fail 2: alert triggered
    with patch("systor.services.uptime_service.ping_target", return_value=(False, 500, 20.0, "Internal Server Error")):
        # Force interval elapsed
        _last_check_ts.clear()
        run_single_audit_cycle(store)
        alerts = store.recent_alerts(limit=5)
        assert len(alerts) == 1
        assert "Critical Service" in alerts[0]["message"]
        assert alerts[0]["severity"] == "alert"

    # Recovery: recover alert triggered
    with patch("systor.services.uptime_service.ping_target", return_value=(True, 200, 15.0, None)):
        _last_check_ts.clear()
        run_single_audit_cycle(store)
        alerts = store.recent_alerts(limit=5)
        assert len(alerts) == 2
        assert alerts[0]["severity"] == "recover"
