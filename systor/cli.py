"""systor CLI: setup, status, send test alerts, etc.

Install via setup.py / pip install . — provides the `systor` command.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

from .config import load_config, save_config
from .metrics import collect_snapshot
from .notifier import send_telegram, send_discord
from .storage import Storage, DEFAULT_DB_PATH
from . import __version__, __app_name__


def _human_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


def _print_status(_args=None):
    cfg = load_config()
    print(f"== {__app_name__} v{__version__} ==")
    print(f"Config:        {cfg.get('logging', {}).get('file', '')}")
    snap = collect_snapshot()
    print(f"Hostname:      {snap.get('hostname')}")
    print(f"Uptime:        {snap.get('uptime_sec', 0) // 3600}h {(snap.get('uptime_sec', 0) % 3600) // 60}m")
    cpu = snap.get("cpu", {})
    print(f"CPU load 1m:   {cpu.get('load_1m')}")
    print(f"CPU temp:      {cpu.get('temp_c')}")
    mem = snap.get("memory", {}) or {}
    print(f"Memory:        {mem.get('used_mb', 0)} / {mem.get('total_mb', 0)} MB used, {mem.get('available_mb', 0)} MB available")
    print(f"Swap:          {mem.get('swap_used_mb', 0)} / {mem.get('swap_total_mb', 0)} MB")
    for d in snap.get("disks", [])[:5]:
        print(f"Disk {d['mount']:20s}  {d['used_pct']}% used ({d['used_gb']:.1f} / {d['size_gb']:.1f} GB)")
    try:
        s = Storage()
        st = s.stats()
        print(f"\nDB:            {st['samples']} samples, {st['alerts']} alerts, {st['notifications']} notifications")
        print(f"DB size:       {_human_bytes(st['db_size_bytes'])}")
    except Exception as e:
        print(f"\nDB:            not available ({e})")


def cmd_setup_telegram(args):
    cfg = load_config()
    cfg["telegram"]["enabled"] = True
    if args.token:
        cfg["telegram"]["bot_token"] = args.token
    if args.chat_id:
        cfg["telegram"]["chat_id"] = args.chat_id
    try:
        path = save_config(cfg)
    except PermissionError as e:
        print(f"❌ Could not save config: {e}")
        print("Hint: run with sudo, or change config path with --config")
        sys.exit(1)
    print(f"✓ Saved to {path}")
    if args.test or args.token or args.chat_id:
        ok, err = send_telegram(cfg["telegram"]["bot_token"], cfg["telegram"]["chat_id"],
                                "🧪 systor test from CLI — Telegram configured successfully")
        if ok:
            print("✓ Test message sent to Telegram")
        else:
            print(f"✗ Telegram send failed: {err}")
            sys.exit(1)


def cmd_setup_discord(args):
    cfg = load_config()
    cfg["discord"]["enabled"] = True
    if args.webhook:
        cfg["discord"]["webhook_url"] = args.webhook
    try:
        path = save_config(cfg)
    except PermissionError as e:
        print(f"❌ Could not save config: {e}")
        sys.exit(1)
    print(f"✓ Saved to {path}")
    if args.test or args.webhook:
        ok, err = send_discord(cfg["discord"]["webhook_url"],
                                "🧪 systor test from CLI — Discord configured successfully")
        if ok:
            print("✓ Test message sent to Discord")
        else:
            print(f"✗ Discord send failed: {err}")
            sys.exit(1)


def cmd_test(args):
    cfg = load_config()
    if args.channel in ("all", "telegram") and cfg["telegram"]["enabled"]:
        ok, err = send_telegram(cfg["telegram"]["bot_token"], cfg["telegram"]["chat_id"],
                                args.message)
        print(f"  telegram: {'✓' if ok else '✗ ' + str(err)}")
    if args.channel in ("all", "discord") and cfg["discord"]["enabled"]:
        ok, err = send_discord(cfg["discord"]["webhook_url"], args.message)
        print(f"  discord:  {'✓' if ok else '✗ ' + str(err)}")
    if not cfg["telegram"]["enabled"] and not cfg["discord"]["enabled"]:
        print("No notification channels enabled. Use `systor setup telegram` or `systor setup discord`.")


def cmd_serve(args):
    """Run the collector or web in foreground (for debugging)."""
    if args.component == "collector":
        from .collector import run
        run()
    elif args.component == "web":
        from .web import run
        run()
    else:
        print(f"Unknown component: {args.component}")
        sys.exit(1)


def cmd_config_show(args):
    cfg = load_config()
    print(json.dumps(cfg, indent=2))


def main():
    p = argparse.ArgumentParser(
        prog=__app_name__,
        description="Lightweight Linux system monitor with web dashboard & alerts",
    )
    p.add_argument("--version", action="version", version=f"{__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show current system state").set_defaults(func=_print_status)

    tg = sub.add_parser("setup", help="configure channels")
    tg_sub = tg.add_subparsers(dest="channel", required=True)
    tg_t = tg_sub.add_parser("telegram", help="configure Telegram")
    tg_t.add_argument("--token", help="bot token (or set SYSTOR_TELEGRAM_BOT_TOKEN)")
    tg_t.add_argument("--chat-id", dest="chat_id", help="chat id (or set SYSTOR_TELEGRAM_CHAT_ID)")
    tg_t.add_argument("--test", action="store_true", help="send test message after save")
    tg_t.set_defaults(func=cmd_setup_telegram)

    tg_d = tg_sub.add_parser("discord", help="configure Discord webhook")
    tg_d.add_argument("--webhook", help="webhook URL (or set SYSTOR_DISCORD_WEBHOOK)")
    tg_d.add_argument("--test", action="store_true", help="send test message after save")
    tg_d.set_defaults(func=cmd_setup_discord)

    t = sub.add_parser("test", help="send a test notification")
    t.add_argument("--channel", choices=["all", "telegram", "discord"], default="all")
    t.add_argument("--message", default="🧪 systor test message", help="message body")
    t.set_defaults(func=cmd_test)

    sv = sub.add_parser("serve", help="run a component in foreground (use systemd normally)")
    sv.add_argument("component", choices=["collector", "web"])
    sv.set_defaults(func=cmd_serve)

    cs = sub.add_parser("config", help="config utilities")
    cs_sub = cs.add_subparsers(dest="config_cmd", required=True)
    cs_show = cs_sub.add_parser("show", help="dump current config as JSON")
    cs_show.set_defaults(func=cmd_config_show)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
