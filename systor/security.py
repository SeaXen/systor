"""Security, IP validation, and trusted proxy enforcement for systor."""
from __future__ import annotations
import ipaddress
import re
from typing import Sequence
from werkzeug.security import check_password_hash


def parse_trusted_proxies(raw: str | Sequence[str] | None) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse comma/newline separated IPs or subnets into network objects."""
    if not raw:
        raw = ["127.0.0.1", "::1"]
    if isinstance(raw, str):
        items = [x.strip() for x in re.split(r"[\n,]+", raw) if x.strip()]
    else:
        items = [str(x).strip() for x in raw if str(x).strip()]

    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in items:
        try:
            # Handle plain IP as /32 or /128
            if "/" not in item:
                addr = ipaddress.ip_address(item)
                item = f"{item}/32" if addr.version == 4 else f"{item}/128"
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    if not networks:
        networks = [ipaddress.ip_network("127.0.0.1/32"), ipaddress.ip_network("::1/128")]
    return networks


def is_ip_in_networks(ip_str: str, networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> bool:
    """Check whether an IP address string falls inside any of the provided networks."""
    try:
        ip = ipaddress.ip_address(ip_str.strip())
        return any(ip in net for net in networks)
    except (ValueError, AttributeError):
        return False


def get_client_ip(req, trusted_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> str:
    """Resolve the genuine client IP, strictly guarding against X-Forwarded-For spoofing.

    If the connecting socket (remote_addr) is NOT a configured trusted proxy,
    any X-Forwarded-For or CF-Connecting-IP headers MUST be discarded.
    """
    peer = (getattr(req, "remote_addr", None) or "").strip()
    if not peer:
        return "127.0.0.1"

    # If peer is NOT in trusted proxies, ignore forwarded headers completely
    if not is_ip_in_networks(peer, trusted_networks):
        return peer

    # Peer is a trusted proxy. Check proxy-specific headers in priority order:
    # 1. CF-Connecting-IP (from Cloudflare)
    cf_ip = req.headers.get("CF-Connecting-IP", "").strip()
    if cf_ip:
        try:
            ipaddress.ip_address(cf_ip)
            return cf_ip
        except ValueError:
            pass

    # 2. X-Forwarded-For: Client, Proxy1, Proxy2...
    xff = req.headers.get("X-Forwarded-For", "").strip()
    if xff:
        # Walk from right to left to find the first untrusted IP
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        for hop in reversed(hops):
            if not is_ip_in_networks(hop, trusted_networks):
                try:
                    ipaddress.ip_address(hop)
                    return hop
                except ValueError:
                    continue
        # If all hops are trusted, use the leftmost hop
        if hops:
            return hops[0]

    return peer


def is_private_ip(ip_str: str) -> bool:
    """Check if an IP is loopback, RFC1918 private, link-local, or Carrier-Grade NAT (Tailscale)."""
    try:
        ip = ipaddress.ip_address(ip_str.strip())
        return (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip in ipaddress.ip_network("100.64.0.0/10")  # CGNAT / Tailscale
        )
    except (ValueError, AttributeError):
        return False


def is_request_private(req, trusted_proxies: str | Sequence[str] | None = None) -> bool:
    """Evaluate whether the request originates from a private/LAN client.

    Cloudflare requests (Cf-Ray or CF-Connecting-IP) routed through tunnels are
    considered public unless explicitly connecting from a private range.
    """
    trusted_nets = parse_trusted_proxies(trusted_proxies)
    client_ip = get_client_ip(req, trusted_nets)

    # Cloudflare tunnel check: If the request came via Cloudflare, check if the CF client is private
    if req.headers.get("Cf-Ray") or req.headers.get("CF-Connecting-IP"):
        cf_ip = req.headers.get("CF-Connecting-IP", "").strip()
        if cf_ip and is_private_ip(cf_ip):
            return True
        return False

    return is_private_ip(client_ip)
