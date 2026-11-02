"""Unit tests for the security audit service (ports, firewall, fail2ban)."""
from unittest.mock import MagicMock, patch
import pytest

from systor.services.security_audit_service import (
    parse_listening_ports_output,
    parse_ufw_output,
    parse_fail2ban_output,
    get_security_audit,
)


def test_parse_listening_ports():
    mock_ss = """State  Recv-Q Send-Q Local Address:Port  Peer Address:PortProcess
LISTEN 0      128          0.0.0.0:22         0.0.0.0:*    users:(("sshd",pid=842,fd=3))
LISTEN 0      511          0.0.0.0:80         0.0.0.0:*    users:(("nginx",pid=1204,fd=6))
LISTEN 0      4096       127.0.0.1:5432       0.0.0.0:*    users:(("postgres",pid=933,fd=7))
"""
    ports = parse_listening_ports_output(mock_ss)
    assert len(ports) == 3
    assert ports[0]["port"] == 22
    assert ports[0]["process"] == "sshd"
    assert ports[0]["pid"] == 842

    assert ports[1]["port"] == 80
    assert ports[1]["process"] == "nginx"

    assert ports[2]["port"] == 5432
    assert ports[2]["address"] == "127.0.0.1"


def test_parse_ufw_output():
    mock_ufw = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere
80/tcp                     ALLOW IN    Anywhere
443/tcp                    ALLOW IN    Anywhere
"""
    fw = parse_ufw_output(mock_ufw)
    assert fw["active"] is True
    assert fw["rules_count"] == 3
    assert "22/tcp" in fw["rules"][0]


def test_parse_fail2ban_output():
    mock_f2b = """Status
|- Number of jail:      2
`- Jail list:   sshd, nginx-http-auth
"""
    f2b = parse_fail2ban_output(mock_f2b)
    assert f2b["active"] is True
    assert f2b["jails_count"] == 2
    assert "sshd" in f2b["jails"]
    assert "nginx-http-auth" in f2b["jails"]


def test_get_security_audit_caching():
    with patch("systor.services.security_audit_service.get_listening_ports", return_value=[{"port": 80}]):
        with patch("systor.services.security_audit_service.get_firewall_status", return_value={"active": True}):
            with patch("systor.services.security_audit_service.get_fail2ban_status", return_value={"active": False}):
                audit = get_security_audit(force_refresh=True)
                assert len(audit["listening_ports"]) == 1
                assert audit["firewall"]["active"] is True
