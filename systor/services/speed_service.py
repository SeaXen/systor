"""Speedtest live runner and background process management."""
from __future__ import annotations
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Callable

log = logging.getLogger("systor.services.speed")

_speed_live_lock = threading.Lock()
_speed_live_proc: subprocess.Popen | None = None
_speed_live_state: dict = {
    "running": False,
    "phase": "idle",
    "progress": 0,
    "target": "",
    "server_id": "",
    "ping_ms": None,
    "jitter_ms": None,
    "packet_loss": None,
    "dl_mbps": None,
    "ul_mbps": None,
    "ok": None,
    "status": "idle",
    "cancel_requested": False,
    "result_url": "",
    "run_type": "manual",
    "started_ts": 0,
    "ended_ts": 0,
}


def speed_live_copy() -> dict:
    with _speed_live_lock:
        return dict(_speed_live_state)


def speed_live_update(**kwargs):
    with _speed_live_lock:
        _speed_live_state.update(kwargs)


def speed_live_reset(server_id: str = "", target: str = "", run_type: str = "manual"):
    with _speed_live_lock:
        _speed_live_state.clear()
        _speed_live_state.update({
            "running": False,
            "phase": "idle",
            "progress": 0,
            "target": target or "",
            "server_id": str(server_id or ""),
            "ping_ms": None,
            "jitter_ms": None,
            "packet_loss": None,
            "dl_mbps": None,
            "ul_mbps": None,
            "ok": None,
            "status": "idle",
            "cancel_requested": False,
            "result_url": "",
            "run_type": run_type or "manual",
            "started_ts": 0,
            "ended_ts": 0,
        })


def speed_live_env() -> dict:
    env = dict(os.environ)
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin")
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("LC_ALL", "C.UTF-8")
    env.setdefault("HOME", "/root")
    return env


def speed_live_parse_line(line: str):
    line = (line or "").strip()
    if not line:
        return
    m = re.search(r"Server:\s+(.+?)\s+\(id:\s*(\d+)\)", line)
    if m:
        speed_live_update(target=m.group(1).replace(" - ", " · "), server_id=m.group(2))
        return
    m = re.search(r"Idle Latency:\s*([0-9.]+)\s*ms\s*\(jitter:\s*([0-9.]+)ms", line)
    if m:
        speed_live_update(phase="ping", progress=25, ping_ms=float(m.group(1)), jitter_ms=float(m.group(2)), status="ping complete")
        return
    m = re.search(r"Download:\s+([0-9.]+)\s+Mbps.*?(\d+)%", line)
    if m:
        speed_live_update(phase="download", progress=75, dl_mbps=float(m.group(1)), status="download running")
        return
    m = re.search(r"Upload:\s+([0-9.]+)\s+Mbps.*?(\d+)%", line)
    if m:
        speed_live_update(phase="upload", progress=75, ul_mbps=float(m.group(1)), status="upload running")
        return
    m = re.search(r"Download:\s+([0-9.]+)\s+Mbps\s+\(data used:", line)
    if m:
        speed_live_update(phase="download", progress=75, dl_mbps=float(m.group(1)), status="download complete")
        return
    m = re.search(r"Upload:\s+([0-9.]+)\s+Mbps\s+\(data used:", line)
    if m:
        speed_live_update(phase="upload", progress=75, ul_mbps=float(m.group(1)), status="upload complete")
        return
    m = re.search(r"Packet Loss:\s*([0-9.]+)%", line)
    if m:
        speed_live_update(packet_loss=float(m.group(1)))
        return
    m = re.search(r"Result URL:\s*(https?://\S+)", line)
    if m:
        speed_live_update(result_url=m.group(1))


def speed_live_finalize(returncode: int, on_success_callback: Callable[[dict], None] | None = None):
    global _speed_live_proc
    snap = speed_live_copy()
    cancelled = bool(snap.get("cancel_requested"))
    ok = (returncode == 0) and not cancelled
    if ok:
        row = {
            "ts": int(snap.get("started_ts") or time.time()),
            "provider": "ookla",
            "mode": "wan",
            "target": snap.get("target") or f"server {snap.get('server_id') or 'auto'}",
            "server_id": snap.get("server_id") or "",
            "run_type": snap.get("run_type") or "manual",
            "ping_ms": snap.get("ping_ms"),
            "jitter_ms": snap.get("jitter_ms"),
            "packet_loss": snap.get("packet_loss"),
            "dl_mbps": snap.get("dl_mbps"),
            "ul_mbps": snap.get("ul_mbps"),
            "ok": True,
            "note": snap.get("result_url") or "live ookla run",
            "raw_json": json.dumps(snap)[:16000],
        }
        if on_success_callback:
            try:
                on_success_callback(row)
            except Exception as e:
                log.warning("speed callback failed: %s", e)
        speed_live_update(ok=True, running=False, phase="complete", progress=100, status="done · results updated", ended_ts=int(time.time()))
    elif cancelled:
        speed_live_update(ok=False, running=False, phase="stopped", progress=0, status="stopped", ended_ts=int(time.time()))
    else:
        speed_live_update(ok=False, running=False, phase="failed", progress=100, status=f"failed · exit {returncode}", ended_ts=int(time.time()))
    with _speed_live_lock:
        _speed_live_proc = None


def speed_live_worker(server_id: str, run_type: str = "manual", on_success: Callable[[dict], None] | None = None):
    global _speed_live_proc
    sid = re.sub(r"[^0-9]", "", str(server_id or ""))
    cmd = ["speedtest", "--accept-license", "--accept-gdpr"]
    if sid:
        cmd += ["-s", sid]
    pretty_target = f"server {sid}" if sid else "auto"
    speed_live_update(
        running=True, phase="starting", progress=6, status="starting…",
        server_id=sid, target=pretty_target, run_type=run_type or "manual", started_ts=int(time.time())
    )
    if not shutil.which("speedtest"):
        speed_live_update(running=False, ok=False, phase="failed", progress=0, status="failed · Ookla CLI not installed", ended_ts=int(time.time()))
        return
    wrapped = ["script", "-q", "-c", " ".join(cmd), "/dev/null"]
    proc = subprocess.Popen(wrapped, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=speed_live_env(), bufsize=1)
    with _speed_live_lock:
        _speed_live_proc = proc
    buf = ""
    try:
        while True:
            ch = proc.stdout.read(1) if proc.stdout else ""
            if ch == "" and proc.poll() is not None:
                break
            if not ch:
                continue
            if ch in "\r\n":
                if buf.strip():
                    speed_live_parse_line(buf)
                buf = ""
            else:
                buf += ch
        if buf.strip():
            speed_live_parse_line(buf)
        rc = proc.wait(timeout=5)
    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        speed_live_update(ok=False, running=False, phase="failed", progress=100, status=f"failed · {e}", ended_ts=int(time.time()))
        with _speed_live_lock:
            _speed_live_proc = None
        return
    speed_live_finalize(rc, on_success)


def stop_speed_live():
    global _speed_live_proc
    speed_live_update(cancel_requested=True, status="stopping…")
    with _speed_live_lock:
        p = _speed_live_proc
    if p:
        try:
            p.terminate()
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def start_speed_live(server_id: str, run_type: str = "manual", on_success: Callable[[dict], None] | None = None) -> bool:
    with _speed_live_lock:
        if _speed_live_state.get("running"):
            return False
        _speed_live_state["running"] = True
    speed_live_reset(server_id=server_id, target="", run_type=run_type)
    t = threading.Thread(target=speed_live_worker, args=(server_id, run_type, on_success), name="systor-speed-live", daemon=True)
    t.start()
    return True
