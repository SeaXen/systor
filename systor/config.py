"""Configuration loader. YAML files + environment variable overrides.

Resolution order (later wins):
  1. Built-in defaults (DEFAULT_CONFIG below)
  2. /etc/systor/config.yaml
  3. ~/.config/systor/config.yaml
  4. ./systor.yaml (cwd)
  5. Environment variables SYSTOR_* (e.g. SYSTOR_POLL_INTERVAL=15)
"""
import os
import json
from pathlib import Path
from typing import Any

APP_NAME = "systor"

DEFAULT_CONFIG: dict = {
    "collector": {
        "poll_interval_sec": 5,
        "retention_days": 7,           # raw samples kept this long
        "rollup_after_hours": 24,      # when to start averaging into 5-min buckets
        "rollup_retention_days": 90,
    },
    "thresholds": {
        # Each metric has: [enabled, threshold value, duration in minutes]
        # Alert fires when value crosses threshold AND stays there >= duration.
        "cpu_load_1m":      {"enabled": True,  "value": 4.0,  "duration_min": 2},
        "cpu_temp_c":       {"enabled": True,  "value": 85.0, "duration_min": 3},
        "mem_free_mb":      {"enabled": True,  "value": 500,  "duration_min": 2},
        "swap_used_mb":     {"enabled": True,  "value": 4096, "duration_min": 2},
        "disk_used_pct":    {"enabled": True,  "value": 90,   "duration_min": 5},
        "cooldown_sec":     600,   # 10 min between same-metric alerts
    },
    "telegram": {
        "enabled": False,
        "bot_token": "",               # or env SYSTOR_TELEGRAM_BOT_TOKEN
        "chat_id": "",                 # or env SYSTOR_TELEGRAM_CHAT_ID
    },
    "discord": {
        "enabled": False,
        "webhook_url": "",            # or env SYSTOR_DISCORD_WEBHOOK
    },
    "network": {
        "default_hours": 24,
        "default_granularity": "day",
        "default_bar_days": 90,
        "default_table_days": 90,
        "default_iface": "all",
        "auto_refresh_sec": 15,
        "hide_virtual_default": True,
    },
    "dashboard": {
        "default_hours": 6,
        "refresh_sec": 5,
        "chart_refresh_sec": 5,
    },
    "apps": {
        "auto_refresh_sec": 15,
        "default_scope": "all",
        "default_sort": "cpu",
        "default_limit": 10,
    },
    "speed": {
        "page_refresh_sec": 15,
        "default_provider": "ookla",
        "auto_enabled": False,
        "auto_provider": "ookla",
        "auto_interval_min": 180,
        "notify_enabled": False,
        "min_download_mbps": 50.0,
        "min_upload_mbps": 20.0,
        "librespeed_server_id": "",
        "ookla_server_id": "",
        "local_librespeed_port": 8077,
        "history_page_size": 25,
        "iperf_port": 5201,
    },
    "web": {
        # 0.0.0.0 = accessible from LAN. Use 127.0.0.1 for local-only.
        "host": "0.0.0.0",
        "port": 6677,
    },
    "logging": {
        "level": "INFO",
        "file": "/var/log/systor/systor.log",
    },
}


def _yaml_to_dict(text: str) -> dict:
    """Tiny line-oriented YAML-ish parser. Handles `key: value` + 2-space indents.

    Sufficient for our flat config (no lists, no anchors, no multi-line).
    """
    out: dict = {}
    stack = [(-1, out)]
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        # pop stack until parent at indent-2
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent_indent, parent = stack[-1]
        if ":" in line:
            k, _, v = line.lstrip(" ").partition(":")
            k = k.strip()
            v = v.strip()
            if v == "":
                # parent dict
                d: dict = {}
                parent[k] = d
                stack.append((indent, d))
            else:
                # scalar
                if v.lower() in ("true", "false"):
                    val: Any = v.lower() == "true"
                else:
                    try:
                        val = int(v)
                    except ValueError:
                        try:
                            val = float(v)
                        except ValueError:
                            val = v.strip('"').strip("'")
                parent[k] = val
    return out


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base; overlay wins on scalar conflict."""
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _apply_env(cfg: dict) -> None:
    """Override config with SYSTOR_* environment variables.

    For threshold metrics (which are dicts with value/duration_min/enabled),
    the SYSTOR_CPU_LOAD etc. env vars set the "value" field.
    """
    env_map = {
        "SYSTOR_POLL_INTERVAL":          ("collector", "poll_interval_sec", int, None),
        "SYSTOR_RETENTION_DAYS":         ("collector", "retention_days", int, None),
        "SYSTOR_CPU_LOAD":               ("thresholds", "cpu_load_1m", float, "value"),
        "SYSTOR_CPU_TEMP":               ("thresholds", "cpu_temp_c", float, "value"),
        "SYSTOR_MEM_FREE_MB":            ("thresholds", "mem_free_mb", int, "value"),
        "SYSTOR_SWAP_USED_MB":           ("thresholds", "swap_used_mb", int, "value"),
        "SYSTOR_DISK_PCT":               ("thresholds", "disk_used_pct", int, "value"),
        "SYSTOR_COOLDOWN":               ("thresholds", "cooldown_sec", int, None),
        "SYSTOR_TELEGRAM_BOT_TOKEN":     ("telegram", "bot_token", str, None),
        "SYSTOR_TELEGRAM_CHAT_ID":       ("telegram", "chat_id", str, None),
        "SYSTOR_DISCORD_WEBHOOK":        ("discord", "webhook_url", str, None),
        "SYSTOR_NETWORK_HOURS":          ("network", "default_hours", float, None),
        "SYSTOR_NETWORK_REFRESH_SEC":    ("network", "auto_refresh_sec", int, None),
        "SYSTOR_DASHBOARD_HOURS":        ("dashboard", "default_hours", float, None),
        "SYSTOR_DASHBOARD_REFRESH_SEC":  ("dashboard", "refresh_sec", int, None),
        "SYSTOR_DASHBOARD_CHART_SEC":    ("dashboard", "chart_refresh_sec", int, None),
        "SYSTOR_APPS_REFRESH_SEC":       ("apps", "auto_refresh_sec", int, None),
        "SYSTOR_APPS_LIMIT":             ("apps", "default_limit", int, None),
        "SYSTOR_SPEED_REFRESH_SEC":      ("speed", "page_refresh_sec", int, None),
        "SYSTOR_SPEED_AUTO_ENABLED":     ("speed", "auto_enabled", lambda v: str(v).lower() in ("1","true","yes","on"), None),
        "SYSTOR_SPEED_AUTO_MIN":         ("speed", "auto_interval_min", int, None),
        "SYSTOR_WEB_PORT":               ("web", "port", int, None),
        "SYSTOR_WEB_HOST":               ("web", "host", str, None),
    }
    for env_key, (section, key, cast, sub) in env_map.items():
        v = os.environ.get(env_key)
        if v is not None:
            try:
                casted = cast(v)
                if sub is not None and isinstance(cfg.get(section, {}).get(key), dict):
                    cfg[section][key][sub] = casted
                else:
                    cfg[section][key] = casted
            except (ValueError, TypeError):
                pass


CONFIG_PATHS = [
    Path("/etc/systor/config.yaml"),
    Path.home() / ".config" / "systor" / "config.yaml",
    Path.cwd() / "systor.yaml",
]


# Migration map: old flat key → new dict structure
_THRESHOLD_MIGRATION = {
    "cpu_load_1m":        ("sustained_samples_cpu",    4),
    "cpu_temp_c":         ("sustained_samples_temp",   4),
    "mem_free_mb":        ("sustained_samples_mem",    4),
    "swap_used_mb":       ("sustained_samples_swap",   4),
    "disk_used_pct":      ("sustained_samples_disk",   1),
}


def _migrate_thresholds(cfg: dict) -> None:
    """If the loaded config has the old flat threshold schema, convert to the new dict form."""
    th = cfg.get("thresholds", {})
    if not th:
        return
    needs_migration = False
    for key, val in th.items():
        if key in ("cooldown_sec",):
            continue
        if not isinstance(val, dict):
            needs_migration = True
            break
    if not needs_migration:
        return
    # Convert each flat value to dict, looking up default duration
    new_th = {}
    for key, val in th.items():
        if key == "cooldown_sec":
            new_th[key] = val
            continue
        if isinstance(val, dict):
            new_th[key] = val
            continue
        # Flat value — convert
        dur_key, default_dur = _THRESHOLD_MIGRATION.get(key, (None, 2))
        if dur_key and dur_key in th:
            try:
                dur_min = max(1, int(int(th[dur_key]) * 0.5))  # samples * 30s / 60s, min 1
            except (ValueError, TypeError):
                dur_min = default_dur
        else:
            dur_min = default_dur
        new_th[key] = {"enabled": True, "value": val, "duration_min": dur_min}
    cfg["thresholds"] = new_th


def load_config() -> dict:
    """Load config with env overrides. Returns a fresh dict each call.

    Migrates old flat-threshold schema to the new dict schema on the fly.
    """
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    for path in CONFIG_PATHS:
        if path.exists() and path.is_file():
            try:
                text = path.read_text()
                overlay = _yaml_to_dict(text)
                cfg = _deep_merge(cfg, overlay)
            except Exception as e:
                import sys
                print(f"[systor] warn: failed to parse {path}: {e}", file=sys.stderr)
    _migrate_thresholds(cfg)
    _apply_env(cfg)
    return cfg


def save_config(cfg: dict, path: Path | None = None) -> Path:
    """Persist config to disk. Default path: /etc/systor/config.yaml."""
    if path is None:
        path = CONFIG_PATHS[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# systor config (written by systor CLI)\n"]
    for section, values in cfg.items():
        lines.append(f"{section}:\n")
        for k, v in values.items():
            if isinstance(v, dict):
                lines.append(f"  {k}:\n")
                for k2, v2 in v.items():
                    lines.append(f"    {k2}: {_scalar_repr(v2)}\n")
            else:
                lines.append(f"  {k}: {_scalar_repr(v)}\n")
    path.write_text("".join(lines))
    return path


def _scalar_repr(v: Any) -> str:
    if isinstance(v, bool): return "true" if v else "false"
    if isinstance(v, (int, float)): return str(v)
    return f'"{v}"'
