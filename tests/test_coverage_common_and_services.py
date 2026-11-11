from unittest.mock import patch
from systor.blueprints.common import (
    parse_hours_arg, bucket_series_points, bucket_network_points,
    login_rate_fail, login_rate_status, login_rate_success, _login_rate_state
)
from systor.services.smart_service import parse_smartctl_record, get_drive_health
from systor.services.ssl_service import probe_remote_cert, get_ssl_certificates
from systor.services.security_audit_service import (
    get_security_audit, parse_ufw_output, parse_fail2ban_output, parse_listening_ports_output
)


def test_common_helpers_and_rate_limits():
    """Test parse_hours_arg, bucketing, and login rate limiting."""
    # parse_hours_arg
    assert parse_hours_arg("6", default=1) == 6.0
    assert parse_hours_arg("invalid", default=2.5) == 2.5
    assert parse_hours_arg(None, default=3) == 24.0 * 3650
    assert parse_hours_arg("0.05", default=1) == 0.1  # clamped min 0.1

    # bucket_series_points (expects (ts, val) tuples)
    raw_points = [(1000 + i * 10, i) for i in range(100)]
    bucketed = bucket_series_points(raw_points, limit=20)
    assert len(bucketed) <= 20
    assert len(bucketed) > 0

    # bucket_network_points (expects dicts)
    net_points = [{"ts": 1000 + i * 10, "rx_mbps": 1.0, "tx_mbps": 2.0} for i in range(100)]
    net_bucketed = bucket_network_points(net_points, limit=20)
    assert len(net_bucketed) <= 20
    assert len(net_bucketed) > 0

    # Login rate limiting
    ip = "192.168.10.99"
    user = "rate_test_user"
    _login_rate_state.clear()

    blocked, wait = login_rate_status(ip, user)
    assert blocked is False

    # Simulate repeated failures
    for _ in range(6):
        b, _ = login_rate_fail(ip, user)

    assert b is True
    blocked_now, wait_now = login_rate_status(ip, user)
    assert blocked_now is True
    assert wait_now > 0

    # Reset on success
    login_rate_success(ip, user)
    b_after, _ = login_rate_status(ip, user)
    assert b_after is False


def test_smart_service_parsing():
    """Test SMART output parser for SATA and NVMe."""
    mock_sata_data = {
        "device": {"type": "ata", "protocol": "ATA"},
        "model_name": "Crucial MX500",
        "serial_number": "1948E2345",
        "user_capacity": {"bytes": 1000000000000},
        "smart_status": {"passed": True},
        "temperature": {"current": 35},
        "power_on_time": {"hours": 5000},
        "ata_smart_attributes": {
            "table": [
                {"id": 5, "name": "Reallocated_Sector_Ct", "raw": {"value": 0}},
                {"id": 197, "name": "Current_Pending_Sector", "raw": {"value": 0}},
            ]
        }
    }
    sata_res = parse_smartctl_record("sda", mock_sata_data)
    assert sata_res["passed"] is True
    assert sata_res["temperature_c"] == 35
    assert sata_res["reallocated_sectors"] == 0

    with patch("shutil.which", return_value=None):
        health = get_drive_health(force_refresh=True)
        assert isinstance(health, list)


def test_ssl_and_security_audit_services(tmp_path):
    """Test SSL certificate tracker and system security audit."""
    # probe_remote_cert with invalid target
    cert_info = probe_remote_cert("invalid.nonexistent.domain.local", port=443, timeout=1.0)
    assert cert_info["valid"] is False
    assert cert_info["status"] == "error"

    # get_ssl_certificates
    cfg_targets = ["example.com:443", "test.local"]
    with patch("systor.services.ssl_service.probe_remote_cert") as mock_probe:
        mock_probe.return_value = {"ok": True, "target": "example.com:443", "days_remaining": 60, "valid": True}
        results = get_ssl_certificates(cfg_targets)
        assert len(results) == 2

    # parse_ufw_output
    ufw_raw = "Status: active\n--\n22/tcp ALLOW IN Anywhere\n80/tcp ALLOW IN Anywhere"
    ufw = parse_ufw_output(ufw_raw)
    assert ufw["active"] is True
    assert ufw["rules_count"] == 2

    # parse_fail2ban_output
    f2b_raw = "Status\n|- Number of jail: 2\n`- Jail list: sshd, nginx"
    f2b = parse_fail2ban_output(f2b_raw)
    assert f2b["active"] is True
    assert "sshd" in f2b["jails"]

    # parse_listening_ports_output
    ss_raw = "Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\ntcp LISTEN 0 128 0.0.0.0:80 0.0.0.0:* users:((\"nginx\",pid=123,fd=6))"
    ports = parse_listening_ports_output(ss_raw)
    assert len(ports) == 1
    assert ports[0]["port"] == 80

    # get_security_audit
    audit_data = get_security_audit(force_refresh=True)
    assert "listening_ports" in audit_data
    assert "firewall" in audit_data
    assert "fail2ban" in audit_data
