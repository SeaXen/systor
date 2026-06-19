from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path


def _cmd_exists(name: str) -> bool:
    return shutil.which(name) is not None


def _run(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _safe_float(v):
    try:
        return float(v)
    except Exception:
        return None


def _http_probe(name: str, url: str, read_bytes: int = 262144, timeout: int = 25) -> dict:
    ts = int(time.time())
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "systor-speed/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(read_bytes)
        dt = max(0.001, time.time() - t0)
        dl_mbps = round((len(data) * 8) / (dt * 1_000_000), 3)
        return {
            "ts": ts,
            "provider": name,
            "mode": "http",
            "target": url,
            "ping_ms": round(dt * 1000, 1),
            "dl_mbps": dl_mbps,
            "ul_mbps": None,
            "ok": True,
            "note": f"HTTP {r.status} · {r.headers.get('server') or 'server ?'} · {len(data)} bytes",
            "raw_json": json.dumps({
                "status": r.status,
                "server": r.headers.get("server"),
                "content_type": r.headers.get("content-type"),
                "content_length": r.headers.get("content-length"),
                "bytes": len(data),
                "seconds": round(dt, 3),
            }),
        }


def run_speedtest_cli() -> dict:
    ts = int(time.time())
    if not _cmd_exists("speedtest-cli"):
        return {"ts": ts, "provider": "speedtest", "mode": "wan", "target": "speedtest-cli", "ok": False, "note": "speedtest-cli not installed", "raw_json": ""}
    p = _run(["speedtest-cli", "--json", "--secure"], timeout=180)
    if p.returncode != 0:
        return {"ts": ts, "provider": "speedtest", "mode": "wan", "target": "speedtest-cli", "ok": False, "note": (p.stderr or p.stdout or f"exit {p.returncode}").strip()[:280], "raw_json": p.stdout[:4000]}
    j = json.loads(p.stdout)
    srv = j.get("server", {}) or {}
    target = f"{srv.get('sponsor', 'server')} · {srv.get('name', '')}, {srv.get('country', '')}".strip(" ·,")
    return {
        "ts": ts,
        "provider": "speedtest",
        "mode": "wan",
        "target": target,
        "ping_ms": _safe_float(j.get("ping")),
        "dl_mbps": round(float(j.get("download", 0)) / 1_000_000, 3),
        "ul_mbps": round(float(j.get("upload", 0)) / 1_000_000, 3),
        "ok": True,
        "note": f"server id {srv.get('id', '?')} · {srv.get('host', '?')}",
        "raw_json": p.stdout[:12000],
    }


def run_librespeed(server_id: str | int | None = None) -> dict:
    ts = int(time.time())
    if not _cmd_exists("librespeed-cli"):
        return {"ts": ts, "provider": "librespeed", "mode": "wan", "target": "librespeed-cli", "ok": False, "note": "librespeed-cli not installed", "raw_json": ""}
    cmd = ["librespeed-cli", "--json"]
    if server_id not in (None, "", "0"):
        cmd += ["--server", str(server_id)]
    p = _run(cmd, timeout=180)
    raw = (p.stdout or "").strip()
    if p.returncode != 0:
        return {"ts": ts, "provider": "librespeed", "mode": "wan", "target": f"server {server_id or 'auto'}", "ok": False, "note": (p.stderr or p.stdout or f"exit {p.returncode}").strip()[:280], "raw_json": p.stdout[:4000]}
    if raw in ("", "null"):
        return {"ts": ts, "provider": "librespeed", "mode": "wan", "target": f"server {server_id or 'auto'}", "ok": False, "note": "no usable LibreSpeed server response", "raw_json": raw}
    j = json.loads(raw)
    if isinstance(j, list):
        j = j[0] if j else {}
    srv = j.get("server", {}) or {}
    return {
        "ts": ts,
        "provider": "librespeed",
        "mode": "wan",
        "target": f"{srv.get('name', 'server')} ({srv.get('url', '')})".strip(),
        "ping_ms": _safe_float(j.get("ping")),
        "dl_mbps": _safe_float(j.get("download")),
        "ul_mbps": _safe_float(j.get("upload")),
        "ok": True,
        "note": f"jitter {j.get('jitter', '?')} ms",
        "raw_json": raw[:12000],
    }


def run_provider(provider: str, cfg: dict | None = None) -> dict:
    provider = (provider or "speedtest").strip().lower()
    speed_cfg = ((cfg or {}).get("speed") or {})
    if provider == "speedtest":
        return run_speedtest_cli()
    if provider == "librespeed":
        return run_librespeed(speed_cfg.get("librespeed_server_id"))
    if provider == "notion":
        return _http_probe("notion", "https://www.notion.so/")
    if provider == "cloudflare":
        return _http_probe("cloudflare", "https://cdnjs.cloudflare.com/ajax/libs/jquery/3.7.1/jquery.min.js")
    raise ValueError(f"unknown provider: {provider}")


def run_many(providers: list[str], cfg: dict | None = None) -> list[dict]:
    out = []
    for p in providers:
        try:
            out.append(run_provider(p, cfg=cfg))
        except Exception as e:
            out.append({"ts": int(time.time()), "provider": p, "mode": "wan", "target": p, "ok": False, "note": str(e), "raw_json": ""})
    return out


def iperf_status(port: int = 5201) -> dict:
    try:
        out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True, timeout=5).stdout.splitlines()
        hits = [ln.strip() for ln in out if f":{int(port)}" in ln and "iperf3" in ln]
        return {"running": bool(hits), "port": int(port), "lines": hits[:5]}
    except Exception as e:
        return {"running": False, "port": int(port), "lines": [], "error": str(e)}


def start_iperf_server(port: int = 5201) -> dict:
    st = iperf_status(port)
    if st.get("running"):
        return st | {"started": False}
    subprocess.Popen(["iperf3", "-s", "-p", str(int(port))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    st = iperf_status(port)
    return st | {"started": bool(st.get("running"))}


def local_ipv4s() -> list[str]:
    vals = []
    try:
        out = subprocess.run(["ip", "-4", "addr", "show"], capture_output=True, text=True, timeout=5).stdout.splitlines()
        for line in out:
            line = line.strip()
            if not line.startswith("inet "):
                continue
            part = line.split()[1]
            ip = part.split("/")[0]
            if ip != "127.0.0.1":
                vals.append(ip)
    except Exception:
        pass
    return vals


def speed_alert_triggered(result: dict, cfg: dict) -> tuple[bool, str]:
    speed_cfg = cfg.get("speed", {}) or {}
    min_dl = _safe_float(speed_cfg.get("min_download_mbps"))
    min_ul = _safe_float(speed_cfg.get("min_upload_mbps"))
    dl = _safe_float(result.get("dl_mbps"))
    ul = _safe_float(result.get("ul_mbps"))
    reasons = []
    if min_dl is not None and dl is not None and dl < min_dl:
        reasons.append(f"DL {dl:.2f} < {min_dl:.2f} Mbps")
    if min_ul is not None and ul is not None and ul < min_ul:
        reasons.append(f"UL {ul:.2f} < {min_ul:.2f} Mbps")
    return (bool(reasons), " · ".join(reasons))


def build_speed_alert(result: dict, host: str) -> tuple[str, str]:
    subject = f"🐢 Speed alert · {result.get('provider', 'speed')}"
    body = "\n".join([
        f"Host: {host}",
        f"Provider: {result.get('provider', '?')}",
        f"Target: {result.get('target', '?')}",
        f"DL: {result.get('dl_mbps', '—')} Mbps",
        f"UL: {result.get('ul_mbps', '—')} Mbps",
        f"Ping: {result.get('ping_ms', '—')} ms",
        f"Note: {result.get('note', '')}",
    ])
    return subject, body
