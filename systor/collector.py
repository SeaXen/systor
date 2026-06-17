"""The systor collector daemon.

Polls metrics every N seconds, evaluates sustained threshold violations,
sends alerts, stores everything in SQLite. Lightweight (<30 MB RAM).

Run as a systemd service (see systemd/systor-collector.service).
"""
from __future__ import annotations
import json
import logging
import signal
import sys
import time
from pathlib import Path

from .config import load_config
from .metrics import collect_snapshot
from .notifier import Notifier
from .storage import Storage, DEFAULT_DB_PATH

log = logging.getLogger("systor.collector")
_running = True


def _handle_term(signum, _frame):
    global _running
    log.info("collector: received signal %d, shutting down", signum)
    _running = False


signal.signal(signal.SIGTERM, _handle_term)
signal.signal(signal.SIGINT, _handle_term)


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

    th = cfg["thresholds"]
    poll = cfg["collector"]["poll_interval_sec"]
    storage = Storage(
        db_path=DEFAULT_DB_PATH,
        retention_days=cfg["collector"]["retention_days"],
        rollup_retention_days=cfg["collector"]["rollup_retention_days"],
    )
    notifier = Notifier(cfg)

    log.info("collector: starting (poll=%ds)", poll)
    last_retention_check = 0.0
    while _running:
        t0 = time.time()
        try:
            snap = collect_snapshot()
            storage.insert_sample(snap)
            cpu = snap.get("cpu", {})
            mem = snap.get("memory", {}) or {}
            worst_disk = max(snap.get("disks", []), key=lambda d: d.get("used_pct", 0), default={})

            events = []
            # CPU load (higher is worse)
            r = _eval_metric("cpu_load_1m", cpu.get("load_1m"),
                            th["cpu_load_1m"], th["sustained_samples_cpu"], True, th["cooldown_sec"])
            if r: events.append(("CPU Load", r, _fmt("cpu_load_1m", r[1], th["cpu_load_1m"])))
            # CPU temp (higher is worse)
            r = _eval_metric("cpu_temp_c", cpu.get("temp_c"),
                            th["cpu_temp_c"], th["sustained_samples_temp"], True, th["cooldown_sec"])
            if r: events.append(("CPU Temperature", r, _fmt("cpu_temp_c", r[1], th["cpu_temp_c"])))
            # Memory free (lower is worse)
            r = _eval_metric("mem_free_mb", mem.get("available_mb"),
                            th["mem_free_mb"], th["sustained_samples_mem"], False, th["cooldown_sec"])
            if r: events.append(("Memory", r, _fmt("mem_free_mb", r[1], th["mem_free_mb"])))
            # Swap used (higher is worse)
            r = _eval_metric("swap_used_mb", mem.get("swap_used_mb"),
                            th["swap_used_mb"], th["sustained_samples_swap"], True, th["cooldown_sec"])
            if r: events.append(("Swap", r, _fmt("swap_used_mb", r[1], th["swap_used_mb"])))
            # Disk (higher is worse)
            if worst_disk:
                r = _eval_metric("disk_used_pct", worst_disk.get("used_pct"),
                                th["disk_used_pct"], th["sustained_samples_disk"], True, th["cooldown_sec"])
                if r: events.append(("Disk", r, _fmt("disk_used_pct", r[1], th["disk_used_pct"])))

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
                # Extract metric key for the storage
                metric_key = subj.lower().replace(" ", "_")
                results = notifier.notify(title, msg)
                for ch, ok, err in results:
                    storage.log_notification(ch, ok, err)

            # Retention once per hour
            if t0 - last_retention_check > 3600:
                last_retention_check = t0
                r1, r2 = storage.apply_retention()
                log.info("retention: deleted %d raw + %d rollup rows", r1, r2)

        except Exception as e:
            log.exception("loop error: %s", e)

        # Sleep with frequent interrupt checks
        for _ in range(poll):
            if not _running:
                break
            time.sleep(1)

    log.info("collector: exiting")


if __name__ == "__main__":
    run()
