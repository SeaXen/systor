"""Service Uptime & Blackbox health checker service.

Monitors internal URLs and TCP sockets, records latency and uptime metrics in SQLite,
and triggers alerts on consecutive failures.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("systor.services.uptime")

_uptime_thread: threading.Thread | None = None
_uptime_stop = threading.Event()
_last_check_ts: dict[int, float] = {}
_fail_count: dict[int, int] = {}


def ping_target(target: dict[str, Any]) -> tuple[bool, int | None, float, str | None]:
    """Execute a single ping against a target URL or TCP host:port.
    Returns: (ok, status_code, latency_ms, error_message)
    """
    raw_url = str(target.get("url") or "").strip()
    timeout = float(target.get("timeout_sec") or 5.0)
    expected_status = int(target.get("expected_status") or 200)
    method = str(target.get("method") or "GET").upper()

    start = time.perf_counter()

    # TCP check format: tcp://host:port
    if raw_url.startswith("tcp://"):
        host_port = raw_url[6:]
        host, _, port_str = host_port.partition(":")
        try:
            port = int(port_str)
            with socket.create_connection((host, port), timeout=timeout):
                latency = round((time.perf_counter() - start) * 1000.0, 2)
                return True, 200, latency, None
        except Exception as e:
            latency = round((time.perf_counter() - start) * 1000.0, 2)
            return False, 0, latency, str(e)

    # HTTP/HTTPS check
    url = raw_url
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "http://" + url

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "systor-uptime-monitor/2.5"},
        method=method,
    )

    try:
        # Avoid SSL verification failure on local self-signed homelab certs
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            code = resp.getcode()
            latency = round((time.perf_counter() - start) * 1000.0, 2)
            ok = (code == expected_status) or (expected_status == 200 and 200 <= code < 400)
            return ok, code, latency, None
    except urllib.error.HTTPError as e:
        latency = round((time.perf_counter() - start) * 1000.0, 2)
        ok = (e.code == expected_status)
        return ok, e.code, latency, str(e.reason) if not ok else None
    except urllib.error.URLError as e:
        latency = round((time.perf_counter() - start) * 1000.0, 2)
        return False, None, latency, str(e.reason)
    except Exception as e:
        latency = round((time.perf_counter() - start) * 1000.0, 2)
        return False, None, latency, str(e)


def run_single_audit_cycle(storage) -> None:
    """Run check for all enabled targets that are due."""
    targets = storage.list_uptime_targets(enabled_only=True)
    now = time.time()
    for t in targets:
        tid = t["id"]
        interval = max(10, int(t.get("interval_sec") or 60))
        last = _last_check_ts.get(tid, 0.0)
        if (now - last) >= interval:
            _last_check_ts[tid] = now
            ok, code, lat, err = ping_target(t)
            storage.record_uptime_check(tid, code, lat, ok, err)
            
            # Debounced alert logic
            if not ok:
                fails = _fail_count.get(tid, 0) + 1
                _fail_count[tid] = fails
                if fails == 2:  # Alert on 2 consecutive failures
                    name = t.get("name", f"Target #{tid}")
                    msg = f"Service '{name}' is DOWN: {err or f'HTTP {code}'}"
                    storage.log_alert(f"uptime.{name}", "alert", code or 0, t.get("expected_status", 200), msg)
            else:
                if _fail_count.get(tid, 0) >= 2:
                    name = t.get("name", f"Target #{tid}")
                    storage.log_alert(f"uptime.{name}", "recover", code or 200, t.get("expected_status", 200), f"Service '{name}' is back online.")
                _fail_count[tid] = 0


def _worker_loop():
    from ..blueprints.common import get_storage
    while not _uptime_stop.is_set():
        try:
            store = get_storage()
            run_single_audit_cycle(store)
        except Exception as e:
            log.debug("uptime worker loop error: %s", e)
        _uptime_stop.wait(10.0)


def start_uptime_worker():
    global _uptime_thread
    if _uptime_thread and _uptime_thread.is_alive():
        return
    _uptime_stop.clear()
    _uptime_thread = threading.Thread(target=_worker_loop, daemon=True, name="systor-uptime-worker")
    _uptime_thread.start()
    log.info("uptime worker started")
