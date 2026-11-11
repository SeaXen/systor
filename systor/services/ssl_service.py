"""SSL/TLS Certificate expiration tracking service.

Probes remote HTTPS hostnames or scans local Let's Encrypt certificates to calculate
expiration dates and remaining validity days.
"""
from __future__ import annotations

import datetime as dt
import logging
import socket
import ssl
from pathlib import Path
from typing import Any

log = logging.getLogger("systor.services.ssl")


def _parse_dn(dn_tuple: Any) -> dict[str, str]:
    parts: dict[str, str] = {}
    if isinstance(dn_tuple, (tuple, list)):
        for rdn in dn_tuple:
            if isinstance(rdn, (tuple, list)):
                for item in rdn:
                    if isinstance(item, (tuple, list)) and len(item) == 2:
                        parts[str(item[0])] = str(item[1])
    return parts


def probe_remote_cert(host: str, port: int = 443, timeout: float = 4.0) -> dict[str, Any] | None:
    """Connect to a TLS host and retrieve its certificate details."""
    clean_host = host.strip()
    if clean_host.startswith("https://"):
        clean_host = clean_host[8:]
    clean_host = clean_host.partition("/")[0].partition(":")[0]

    if not clean_host:
        return None

    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((clean_host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=clean_host) as ssock:
                cert = ssock.getpeercert()
                if not cert:
                    return None

                not_after_val = cert.get("notAfter")
                if not isinstance(not_after_val, str):
                    return None

                # Format example: 'May 20 12:00:00 2027 GMT'
                expire_dt = dt.datetime.strptime(not_after_val, "%b %d %H:%M:%S %Y %Z")
                now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                days_left = (expire_dt - now).days

                # Issuer organization
                issuer_parts = _parse_dn(cert.get("issuer"))
                issuer = issuer_parts.get("organizationName") or issuer_parts.get("commonName") or "Unknown"

                # Subject common name
                sub_parts = _parse_dn(cert.get("subject"))
                subject = sub_parts.get("commonName") or clean_host

                # SANs (Subject Alternative Names)
                san_tuple = cert.get("subjectAltName", ())
                sans: list[str] = []
                if isinstance(san_tuple, (tuple, list)):
                    for item in san_tuple:
                        if isinstance(item, (tuple, list)) and len(item) == 2 and item[0] == "DNS":
                            sans.append(str(item[1]))

                status = "ok" if days_left > 30 else ("warn" if days_left > 7 else "critical")

                return {
                    "host": clean_host,
                    "port": port,
                    "subject": subject,
                    "issuer": issuer,
                    "not_after": expire_dt.strftime("%Y-%m-%d %H:%M UTC"),
                    "days_left": days_left,
                    "status": status,
                    "sans": sans[:5],
                    "valid": days_left > 0,
                    "source": "remote",
                }
    except Exception as e:
        log.debug("TLS probe failed for %s:%d: %s", clean_host, port, e)
        return {
            "host": clean_host,
            "port": port,
            "subject": clean_host,
            "issuer": "Error",
            "not_after": "Unavailable",
            "days_left": -1,
            "status": "error",
            "sans": [],
            "valid": False,
            "error": str(e),
            "source": "remote",
        }


def scan_local_letsencrypt() -> list[dict[str, Any]]:
    """Scan local /etc/letsencrypt/live/ certificates if present."""
    le_dir = Path("/etc/letsencrypt/live")
    certs: list[dict[str, Any]] = []
    if not le_dir.exists():
        return certs

    for domain_dir in le_dir.iterdir():
        if not domain_dir.is_dir():
            continue
        cert_pem = domain_dir / "cert.pem"
        if cert_pem.exists():
            remote = probe_remote_cert(domain_dir.name)
            if remote:
                remote["source"] = "local"
                certs.append(remote)
    return certs


def get_ssl_certificates(custom_targets: list[str] | None = None) -> list[dict[str, Any]]:
    """Aggregate certificate checks for configured hosts and local Let's Encrypt dirs."""
    results: list[dict[str, Any]] = []
    seen_hosts: set[str] = set()

    # Local certs
    for c in scan_local_letsencrypt():
        h = c.get("host")
        if h and h not in seen_hosts:
            seen_hosts.add(h)
            results.append(c)

    # Custom targets passed or configured
    targets = custom_targets or []
    for t in targets:
        if not t or t in seen_hosts:
            continue
        res = probe_remote_cert(t)
        if res:
            seen_hosts.add(t)
            results.append(res)

    return results
