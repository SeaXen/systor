"""The systor collector daemon.

Polls metrics every N seconds, evaluates sustained threshold violations,
sends alerts, stores everything in SQLite. Lightweight (<30 MB RAM).

Hot-reloads thresholds each tick by watching the config file mtime,
so changes from the web UI take effect within one poll interval
without restarting the daemon.

Run as a systemd service (see systemd/systor-collector.service).
"""
from __future__ import annotations
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

from .config import load_config
from .metrics import collect_snapshot, read_top_processes, read_total_memory_mb
from .notifier import Notifier
from .storage import Storage, DEFAULT_DB_PATH
from .speed import run_provider, speed_alert_triggered, build_speed_alert

log = logging.getLogger("systor.collector")
_running = True
_reload_requested = False


def _handle_term(signum, _frame):
    global _running
    log.info("collector: received signal %d, shutting down", signum)
    _running = False


def _handle_hup(signum, _frame):
    global _reload_requested
    log.info("collector: SIGHUP received, will reload config on next tick")
    _reload_requested = True


signal.signal(signal.SIGTERM, _handle_term)
signal.signal(signal.SIGINT, _handle_term)
signal.signal(signal.SIGHUP, _handle_hup)


# Per-metric state: how many consecutive samples above/below threshold, last alert time, in_alert flag
_metric_state: dict[str, dict] = {}


def _eval_metric(name: str, value, threshold, sustained_needed, higher_is_worse: bool, cooldown_sec: int):
    """Update state for a metric; return ('alert', value, threshold) / ('recover', value) / None."""
    s = _metric_state.setdefault(name, {"count": 0, "in_alert": False, "last_alert_ts": 0.0})
    now = time.time()
    if value is None:
        s["count"] = 0
        return None
    is_bad = (value > threshold) if higher_is_worse else (value < threshold)
    if is_bad:
        s["count"] += 1
    else:
        s["count"] = 0
    if s["count"] >= sustained_needed:
        if not s["in_alert"] or (now - s["last_alert_ts"]) > cooldown_sec:
            s["in_alert"] = True
            s["last_alert_ts"] = now
            return ("alert", value, threshold)
    elif s["in_alert"]:
        s["in_alert"] = False
        return ("recover", value, threshold)
    return None


def _fmt(metric: str, value, threshold) -> str:
    unit = {
        "cpu_load_1m": "", "cpu_temp_c": "°C", "mem_free_mb": " MB",
        "swap_used_mb": " MB", "disk_used_pct": "%",
    }.get(metric, "")
    arrow = ">" if metric in ("cpu_load_1m", "cpu_temp_c", "swap_used_mb", "disk_used_pct") else "<"
    if metric == "disk_used_pct":
        return f"Disk usage {value}% (>{threshold}%)"
    pretty = metric.replace("_", " ").title()
    return f"{pretty}: {value}{unit} {arrow} {threshold}{unit}"


def _top_cause_line(metric_key: str) -> str:
    """Compact single-line likely-cause hint. Only CPU OR memory, never both."""
    try:
        if metric_key in ("cpu_load", "cpu_temperature"):
            p = (read_top_processes(n=1, by="cpu") or [{}])[0]
            if p.get("name"):
                return f"Top CPU app: {p['name']} ({p.get('cpu_percent', 0)}% CPU, pid {p.get('pid')})"
        elif metric_key in ("memory", "swap"):
            p = (read_top_processes(n=1, by="mem") or [{}])[0]
            if p.get("name"):
                total_mb = read_total_memory_mb() or 0
                mem_pct = round(100.0 * p.get("mem_mb", 0) / total_mb, 1) if total_mb else 0.0
                return f"Top memory app: {p['name']} ({p.get('mem_mb', 0)} MB, {mem_pct}% RAM, pid {p.get('pid')})"
    except Exception:
        pass
    return ""


def _build_alert_body(subject: str, metric_key: str, kind: str, value, threshold, snap: dict, worst_disk: dict | None, msg: str) -> str:
    host = snap.get("hostname", "host")
    cpu = snap.get("cpu", {}) or {}
    mem = snap.get("memory", {}) or {}
    state_emoji = "🚨" if kind == 'alert' else "✅"
    parts = [
        f"{state_emoji} {host}",
        f"<b>{subject}</b>",
        f"{msg}",
    ]
    if metric_key == "disk":
        if worst_disk:
            parts.append(f"💽 {worst_disk.get('mount', '?')} · {worst_disk.get('used_gb', '?')}/{worst_disk.get('size_gb', '?')} GB")
    elif metric_key == "cpu_load":
        parts.append(f"🧠 CPU {cpu.get('percent', '?')}% · 1m {cpu.get('load_1m', '?')} · 5m {cpu.get('load_5m', '?')}")
    elif metric_key == "cpu_temperature":
        parts.append(f"🌡️ CPU temp {cpu.get('temp_c', '?')}°C")
    elif metric_key == "memory":
        parts.append(f"🧮 RAM free {mem.get('available_mb', '?')} MB · used {mem.get('used_mb', '?')} MB")
    elif metric_key == "swap":
        parts.append(f"📦 Swap used {mem.get('swap_used_mb', '?')} MB")
    cause = _top_cause_line(metric_key)
    if cause and kind == "alert":
        parts.append(f"🔥 {cause.replace('Top CPU app: ','').replace('Top memory app: ','')}")
    return "\n".join(parts)


def _th_metric(th: dict, name: str) -> tuple:
    """Return (value, duration_min, enabled) for a threshold entry.

    Accepts both old flat (float) and new dict {enabled, value, duration_min} forms.
    """
    entry = th.get(name)
    if isinstance(entry, dict):
        return (entry.get("value", 0), int(entry.get("duration_min", 2)),
                bool(entry.get("enabled", True)))
    return (entry or 0, 2, True)


def _samples_for(duration_min: int, poll_sec: int) -> int:
    """How many consecutive samples equal `duration_min` minutes at the given poll interval."""
    if poll_sec <= 0:
        poll_sec = 30
    return max(1, int((duration_min * 60) // poll_sec))


def _config_mtime() -> float:
    """Return mtime of the first existing config file, or 0."""
    from .config import CONFIG_PATHS
    for p in CONFIG_PATHS:
        if p.exists():
            try:
                return p.stat().st_mtime
            except OSError:
                continue
    return 0.0


def run():
    cfg = load_config()
    log_cfg = cfg.get("logging", {})
    logging.basicConfig(
        level=getattr(logging, log_cfg.get("level", "INFO")),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log_file = log_cfg.get("file", "/var/log/systor/systor.log")
    try:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file)
        fh.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        log.addHandler(fh)
    except PermissionError:
        log.warning("cannot write to %s, logging to stdout only", log_file)

    # Write a pidfile so the web UI can SIGHUP/SIGTERM us
    try:
        Path("/tmp/systor-collector.pid").write_text(str(os.getpid()))
        log.info("collector: pidfile at /tmp/systor-collector.pid (pid %d)", os.getpid())
    except OSError as e:
        log.warning("could not write pidfile: %s", e)

    # Pre-warm process CPU% baseline so the first /api/top-processes call
    # has data (otherwise the first call always returns 0% for everything).
    try:
        from .metrics import read_top_processes
        read_top_processes(n=1, by="cpu")
        log.info("collector: pre-warmed process CPU baseline")
    except Exception as e:
        log.debug("pre-warm processes: %s", e)

    last_cfg_mtime = _config_mtime()
    last_retention_check = 0.0
    last_speedtest_check = 0.0

    log.info("collector: starting (poll=%ds)", cfg["collector"]["poll_interval_sec"])
    while _running:
        t0 = time.time()
        try:
            # ---- Hot-reload config if file mtime changed or SIGHUP received ----
            global _reload_requested
            cur_mtime = _config_mtime()
            if _reload_requested or (cur_mtime and cur_mtime > last_cfg_mtime):
                try:
                    new_cfg = load_config()
                    if new_cfg != cfg:
                        # Reset sustained counters so old violations don't bleed
                        _metric_state.clear()
                        log.info("collector: config reloaded from disk")
                    cfg = new_cfg
                    _reload_requested = False
                    last_cfg_mtime = cur_mtime
                except Exception as e:
                    log.warning("config reload failed: %s", e)

            poll = cfg["collector"]["poll_interval_sec"]
            th = cfg["thresholds"]
            storage = Storage(
                db_path=DEFAULT_DB_PATH,
                retention_days=cfg["collector"]["retention_days"],
                rollup_retention_days=cfg["collector"]["rollup_retention_days"],
            )
            notifier = Notifier(cfg)

            snap = collect_snapshot()
            storage.insert_sample(snap)
            cpu = snap.get("cpu", {})
            mem = snap.get("memory", {}) or {}
            worst_disk = max(snap.get("disks", []), key=lambda d: d.get("used_pct", 0), default={})

            events = []
            cooldown = th.get("cooldown_sec", 600)

            def _check(name, value, higher_is_worse):
                v, dur_min, enabled = _th_metric(th, name)
                if not enabled:
                    return None
                return _eval_metric(name, value, v, _samples_for(dur_min, poll),
                                    higher_is_worse, cooldown)

            # CPU load (higher is worse)
            r = _check("cpu_load_1m", cpu.get("load_1m"), True)
            if r: events.append(("CPU Load", r, _fmt("cpu_load_1m", r[1], _th_metric(th, "cpu_load_1m")[0])))
            # CPU temp (higher is worse)
            r = _check("cpu_temp_c", cpu.get("temp_c"), True)
            if r: events.append(("CPU Temperature", r, _fmt("cpu_temp_c", r[1], _th_metric(th, "cpu_temp_c")[0])))
            # Memory free (lower is worse)
            r = _check("mem_free_mb", mem.get("available_mb"), False)
            if r: events.append(("Memory", r, _fmt("mem_free_mb", r[1], _th_metric(th, "mem_free_mb")[0])))
            # Swap used (higher is worse)
            r = _check("swap_used_mb", mem.get("swap_used_mb"), True)
            if r: events.append(("Swap", r, _fmt("swap_used_mb", r[1], _th_metric(th, "swap_used_mb")[0])))
            # Disk (higher is worse)
            if worst_disk:
                r = _check("disk_used_pct", worst_disk.get("used_pct"), True)
                if r: events.append(("Disk", r, _fmt("disk_used_pct", r[1], _th_metric(th, "disk_used_pct")[0])))

            for subj, (kind, value, threshold), msg in events:
                title = f"🔴 {subj} ALERT" if kind == "alert" else f"✅ {subj} recovered"
                log.warning("%s: %s", title, msg)
                storage.log_alert(
                    metric=subj.lower().replace(" ", "_"),
                    severity=kind,
                    value=value,
                    threshold=threshold,
                    message=msg,
                )
                metric_key = subj.lower().replace(" ", "_")
                body = _build_alert_body(subj, metric_key, kind, value, threshold, snap, worst_disk, msg)
                results = notifier.notify(title, body)
                for ch, ok, err in results:
                    storage.log_notification(ch, ok, err)

            speed_cfg = cfg.get("speed", {}) or {}
            if speed_cfg.get("auto_enabled"):
                interval_sec = max(300, int(float(speed_cfg.get("auto_interval_min", 180)) * 60))
                if (t0 - last_speedtest_check) >= interval_sec:
                    provider = str(speed_cfg.get("auto_provider") or "ookla")
                    try:
                        result = run_provider(provider, cfg=cfg, run_type="auto")
                        storage.log_speedtest(result)
                        last_speedtest_check = t0
                        if result.get("ok") and speed_cfg.get("notify_enabled"):
                            bad, reason = speed_alert_triggered(result, cfg)
                            if bad:
                                subject, body = build_speed_alert(result, snap.get("hostname", "host"))
                                body = body + "\n" + reason
                                results = notifier.notify(subject, body)
                                for ch, ok, err in results:
                                    storage.log_notification(ch, ok, err)
                    except Exception as e:
                        log.warning("speed auto check failed: %s", e)

            # Retention once per hour
            if t0 - last_retention_check > 3600:
                last_retention_check = t0
                r1, r2 = storage.apply_retention()
                log.info("retention: deleted %d raw + %d rollup rows", r1, r2)

        except Exception as e:
            log.exception("loop error: %s", e)

        # Sleep with frequent interrupt checks
        poll = cfg["collector"]["poll_interval_sec"]
        for _ in range(poll):
            if not _running:
                break
            time.sleep(1)

    # Cleanup pidfile
    try:
        Path("/tmp/systor-collector.pid").unlink(missing_ok=True)
    except OSError:
        pass
    log.info("collector: exiting")


if __name__ == "__main__":
    run()
