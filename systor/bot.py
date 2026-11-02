"""Two-way interactive Telegram Bot Daemon for Systor.

Allows homelab administrators to query system health, trigger speedtests,
and inspect active alerts directly from their mobile Telegram client.
Uses pure Python standard library (urllib.request) with zero pip dependencies.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
import urllib.parse

from .metrics import collect_snapshot
from .storage import Storage, DEFAULT_DB_PATH

log = logging.getLogger("systor.bot")

_BOT_THREAD: threading.Thread | None = None
_BOT_STOP_EVENT = threading.Event()


def send_telegram_message(bot_token: str, chat_id: str, text: str, parse_mode: str = "Markdown") -> bool:
    """Send a message via Telegram Bot API."""
    if not bot_token or not chat_id or not text:
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": str(chat_id),
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        log.warning("Telegram send failed: %s", e)
        return False


def handle_bot_command(cmd_text: str, storage: Storage | None = None) -> str:
    """Parse and generate response for bot commands."""
    cmd = cmd_text.strip().split()[0].lower() if cmd_text.strip() else ""

    if cmd in ("/start", "/help"):
        return (
            "*Systor Server Bot*\n\n"
            "Available commands:\n"
            "• `/status` - Live CPU, RAM, Disk, and Load telemetry\n"
            "• `/speed` - Run instant network speedtest\n"
            "• `/alerts` - View recent unresolved alerts\n"
            "• `/ping` - Healthcheck response"
        )

    if cmd == "/ping":
        return "🏓 *Pong!* Systor daemon is running."

    if cmd == "/status":
        snap = collect_snapshot()
        host = snap.get("hostname", "systor")
        up_sec = snap.get("uptime_sec", 0)
        up_str = f"{up_sec // 3600}h {(up_sec % 3600) // 60}m"
        cpu = snap.get("cpu", {})
        mem = snap.get("memory", {})
        mem_pct = round(100.0 * mem.get("used_mb", 0) / max(1, mem.get("total_mb", 1)), 1)
        disks = snap.get("disks", [])
        disk_str = " · ".join(f"{d['mount']}: {d['used_pct']}%" for d in disks[:3]) or "N/A"

        return (
            f"🖥️ *{host} System Status*\n\n"
            f"• *Uptime:* `{up_str}`\n"
            f"• *CPU Usage:* `{cpu.get('percent', 0)}%` (Load: `{cpu.get('load_1m', 0)} / {cpu.get('load_5m', 0)}`)\n"
            f"• *Temp:* `{cpu.get('temp_c', 'N/A')}°C`\n"
            f"• *Memory:* `{mem.get('used_mb', 0)} / {mem.get('total_mb', 0)} MB` (`{mem_pct}%`)\n"
            f"• *Disks:* `{disk_str}`"
        )

    if cmd == "/alerts":
        if not storage:
            storage = Storage(DEFAULT_DB_PATH)
        alerts = storage.recent_alerts(limit=5)
        if not alerts:
            return "✅ *System Healthy*: No active or recent threshold alerts."
        lines = ["⚠️ *Recent Alerts:*"]
        for a in alerts:
            ts_str = time.strftime("%H:%M:%S", time.localtime(a.get("ts", time.time())))
            lines.append(f"• `{ts_str}` *{a.get('metric')}*: {a.get('message')}")
        return "\n".join(lines)

    if cmd == "/speed":
        try:
            from .speed import run_provider
            res = run_provider("speedtest_cli")
            if not res or not res.get("ok"):
                res = run_provider("fast_cli")
            if res and res.get("ok"):
                return (
                    f"⚡ *Speedtest Complete*\n\n"
                    f"• *Download:* `{res.get('download_mbps', 0):.1f} Mbps`\n"
                    f"• *Upload:* `{res.get('upload_mbps', 0):.1f} Mbps`\n"
                    f"• *Ping:* `{res.get('ping_ms', 0):.1f} ms`"
                )
            return "❌ Speedtest failed or no speedtest tool available on host."
        except Exception as e:
            return f"❌ Speedtest error: {e}"

    return "Unknown command. Type `/help` for available commands."


def bot_poll_worker(bot_token: str, authorized_chat_id: str, storage: Storage | None = None):
    """Long-polling worker loop for Telegram updates."""
    log.info("Telegram interactive bot daemon started for chat ID %s", authorized_chat_id)
    offset = 0
    clean_auth_id = str(authorized_chat_id).strip()

    while not _BOT_STOP_EVENT.is_set():
        try:
            url = f"https://api.telegram.org/bot{bot_token}/getUpdates?offset={offset}&timeout=15"
            req = urllib.request.Request(url, headers={"User-Agent": "Systor-Bot/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                if resp.status != 200:
                    time.sleep(5)
                    continue
                data = json.loads(resp.read().decode("utf-8"))

            for update in data.get("result", []):
                offset = max(offset, update.get("update_id", 0) + 1)
                msg = update.get("message") or update.get("channel_post")
                if not msg:
                    continue

                chat = msg.get("chat", {})
                chat_id = str(chat.get("id", "")).strip()

                # Verify sender chat_id
                if clean_auth_id and chat_id != clean_auth_id:
                    log.warning("Rejected unauthorized telegram message from chat ID: %s", chat_id)
                    continue

                text = msg.get("text", "").strip()
                if text.startswith("/"):
                    reply = handle_bot_command(text, storage)
                    send_telegram_message(bot_token, chat_id, reply)

        except urllib.error.URLError:
            # Network blip or timeout, wait briefly
            time.sleep(5)
        except Exception as e:
            log.debug("Telegram poll loop exception: %s", e)
            time.sleep(5)


def start_bot_daemon(cfg: dict, storage: Storage | None = None) -> threading.Thread | None:
    """Start the interactive bot thread if enabled in configuration."""
    global _BOT_THREAD
    tg_cfg = cfg.get("telegram", {})
    if not tg_cfg.get("enabled"):
        return None

    bot_token = tg_cfg.get("bot_token", "").strip()
    chat_id = str(tg_cfg.get("chat_id", "")).strip()
    if not bot_token or not chat_id:
        log.warning("Telegram bot enabled but bot_token or chat_id is missing.")
        return None

    _BOT_STOP_EVENT.clear()
    thread = threading.Thread(
        target=bot_poll_worker,
        args=(bot_token, chat_id, storage),
        daemon=True,
        name="systor-bot",
    )
    thread.start()
    _BOT_THREAD = thread
    return thread


def stop_bot_daemon():
    """Signal the bot daemon thread to terminate."""
    _BOT_STOP_EVENT.set()
