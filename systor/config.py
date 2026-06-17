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
        "poll_interval_sec": 30,
        "retention_days": 7,           # raw samples kept this long
        "rollup_after_hours": 24,      # when to start averaging into 5-min buckets
        "rollup_retention_days": 90,
    },
    "thresholds": {
        "cpu_load_1m": 4.0,
        "cpu_temp_c": 80.0,
        "mem_free_mb": 500,
        "swap_used_mb": 4096,
        "disk_used_pct": 90,
        "sustained_samples_cpu": 4,    # 4 * 30s = 2 min
        "sustained_samples_temp": 4,
        "sustained_samples_mem": 4,
        "sustained_samples_swap": 4,
        "sustained_samples_disk": 1,   # disk fills slowly
        "cooldown_sec": 600,           # 10 min between same-metric alerts
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
    "web": {
        "host": "127.0.0.1",
        "port": 6677,
        "auth_enabled": False,
        "auth_password_hash": "",     # bcrypt; use `systor web set-password`
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
    """Override config with SYSTOR_* environment variables (dot notation)."""
    env_map = {
        "SYSTOR_POLL_INTERVAL":          ("collector", "poll_interval_sec", int),
        "SYSTOR_RETENTION_DAYS":         ("collector", "retention_days", int),
        "SYSTOR_CPU_LOAD":               ("thresholds", "cpu_load_1m", float),
        "SYSTOR_CPU_TEMP":               ("thresholds", "cpu_temp_c", float),
        "SYSTOR_MEM_FREE_MB":            ("thresholds", "mem_free_mb", int),
        "SYSTOR_SWAP_USED_MB":           ("thresholds", "swap_used_mb", int),
        "SYSTOR_DISK_PCT":               ("thresholds", "disk_used_pct", int),
        "SYSTOR_COOLDOWN":               ("thresholds", "cooldown_sec", int),
        "SYSTOR_TELEGRAM_BOT_TOKEN":     ("telegram", "bot_token", str),
        "SYSTOR_TELEGRAM_CHAT_ID":       ("telegram", "chat_id", str),
        "SYSTOR_DISCORD_WEBHOOK":        ("discord", "webhook_url", str),
        "SYSTOR_WEB_PORT":               ("web", "port", int),
        "SYSTOR_WEB_HOST":               ("web", "host", str),
    }
    for env_key, (section, key, cast) in env_map.items():
        v = os.environ.get(env_key)
        if v is not None:
            try:
                cfg[section][key] = cast(v)
            except (ValueError, TypeError):
                pass


CONFIG_PATHS = [
    Path("/etc/systor/config.yaml"),
    Path.home() / ".config" / "systor" / "config.yaml",
    Path.cwd() / "systor.yaml",
]


def load_config() -> dict:
    """Load config with env overrides. Returns a fresh dict each call."""
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
