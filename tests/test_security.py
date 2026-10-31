"""Tests for security, trusted proxies, and path traversal protection."""
import pytest
from unittest.mock import MagicMock

from systor.security import (
    parse_trusted_proxies,
    is_ip_in_networks,
    get_client_ip,
    is_private_ip,
    is_request_private,
)
from systor.services.storage_service import (
    auto_storage_roots,
    is_under,
    resolve_storage_path,
)


def test_parse_trusted_proxies():
    # String with commas and spaces
    nets = parse_trusted_proxies("127.0.0.1, 10.0.0.0/8, ::1")
    assert len(nets) == 3

    # Sequence of strings
    nets2 = parse_trusted_proxies(["192.168.1.0/24", "127.0.0.1"])
    assert len(nets2) == 2

    # None or empty falls back to localhost networks
    default_nets = parse_trusted_proxies(None)
    assert len(default_nets) == 2
    assert len(parse_trusted_proxies("")) == 2

    # Invalid IPs are gracefully skipped without crashing
    nets3 = parse_trusted_proxies("127.0.0.1, invalid_ip, 10.0.0.1")
    assert len(nets3) == 2


def test_is_ip_in_networks():
    nets = parse_trusted_proxies("127.0.0.0/8, 10.0.0.0/16")
    assert is_ip_in_networks("127.0.0.1", nets) is True
    assert is_ip_in_networks("10.0.5.20", nets) is True
    assert is_ip_in_networks("192.168.1.1", nets) is False
    assert is_ip_in_networks("not-an-ip", nets) is False


def test_get_client_ip_direct_untrusted():
    # Public peer directly connected, sending a fake X-Forwarded-For header
    req = MagicMock()
    req.remote_addr = "203.0.113.50"
    req.headers = {"X-Forwarded-For": "192.168.1.100"}

    # Default trusted proxy is only 127.0.0.1
    trusted = parse_trusted_proxies("127.0.0.1")
    client_ip = get_client_ip(req, trusted)

    # MUST ignore the fake header and return the direct remote_addr
    assert client_ip == "203.0.113.50"


def test_get_client_ip_behind_trusted_proxy():
    # Request coming from local reverse proxy (127.0.0.1)
    req = MagicMock()
    req.remote_addr = "127.0.0.1"
    req.headers = {"X-Forwarded-For": "203.0.113.195"}

    trusted = parse_trusted_proxies("127.0.0.1, ::1")
    client_ip = get_client_ip(req, trusted)
    assert client_ip == "203.0.113.195"


def test_get_client_ip_multi_hop_proxy():
    # Client -> CDN / edge -> local nginx (127.0.0.1)
    req = MagicMock()
    req.remote_addr = "127.0.0.1"
    req.headers = {"X-Forwarded-For": "198.51.100.22, 10.0.0.2"}

    # If 10.0.0.2 is in trusted proxies:
    trusted = parse_trusted_proxies("127.0.0.1, 10.0.0.0/8")
    client_ip = get_client_ip(req, trusted)
    assert client_ip == "198.51.100.22"


def test_is_private_ip():
    assert is_private_ip("127.0.0.1") is True
    assert is_private_ip("::1") is True
    assert is_private_ip("10.1.2.3") is True
    assert is_private_ip("192.168.0.1") is True
    assert is_private_ip("172.20.0.5") is True
    assert is_private_ip("100.64.0.1") is True  # Tailscale CGNAT
    assert is_private_ip("8.8.8.8") is False
    assert is_private_ip("1.1.1.1") is False
    assert is_private_ip("93.184.216.34") is False
    assert is_private_ip("invalid") is False


def test_is_request_private():
    req = MagicMock()
    req.remote_addr = "192.168.1.50"
    req.headers = {}
    assert is_request_private(req, "127.0.0.1") is True

    # Untrusted remote claims to be private via XFF
    req.remote_addr = "8.8.8.8"
    req.headers = {"X-Forwarded-For": "192.168.1.50"}
    assert is_request_private(req, "127.0.0.1") is False


def test_storage_roots_exclude_root_by_default():
    roots = auto_storage_roots()
    # /root should NEVER be in default auto roots
    assert "/root" not in roots


def test_storage_is_under(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    child = root / "sub" / "file.txt"
    outside = tmp_path / "outside.txt"

    assert is_under(root, child) is True
    assert is_under(root, outside) is False


def test_storage_resolve_storage_path_security(monkeypatch, tmp_path):
    safe_root = tmp_path / "safe"
    safe_root.mkdir()
    safe_file = safe_root / "document.txt"
    safe_file.write_text("secure data")

    # Mock safe_roots to return our tmp safe_root
    monkeypatch.setattr(
        "systor.services.storage_service.safe_roots",
        lambda: [safe_root]
    )

    # 1. Accessing file inside safe root succeeds
    rp, code, err = resolve_storage_path(str(safe_file))
    assert code == 200
    assert rp == safe_file.resolve()

    # 2. Path traversal breaking out of root is blocked with 403
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret")
    rp, code, err = resolve_storage_path(str(safe_root / ".." / "outside.txt"))
    assert code == 403
    assert "escapes" in err

    # 3. Secret-like filename (e.g. .env) blocked with 403
    secret_file = safe_root / ".env"
    secret_file.write_text("API_KEY=xyz")
    rp, code, err = resolve_storage_path(str(secret_file))
    assert code == 403
    assert "secret" in err.lower()
