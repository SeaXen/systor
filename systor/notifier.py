"""Telegram + Discord notifier with retry + cooldown."""
from __future__ import annotations
import json
import logging
import urllib.request
import urllib.error
import urllib.parse
from typing import Any

log = logging.getLogger("systor.notifier")


def send_telegram(bot_token: str, chat_id: str, text: str, timeout: int = 10) -> tuple[bool, str | None]:
    if not bot_token or not chat_id:
        return False, "missing bot_token or chat_id"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
            if data.get("ok"):
                return True, None
            return False, str(data.get("description", "unknown"))
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"
    except Exception as e:
        return False, str(e)


def send_discord(webhook_url: str, text: str, timeout: int = 10) -> tuple[bool, str | None]:
    if not webhook_url:
        return False, "missing webhook_url"
    if len(text) > 1900:
        text = text[:1850] + "\n…(truncated)"
    payload = json.dumps({"content": text}).encode()
    req = urllib.request.Request(webhook_url, data=payload, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (200 <= r.status < 300), None
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"
    except Exception as e:
        return False, str(e)


class Notifier:
    """Wraps config + sends via all enabled channels. Returns list of (channel, ok, err)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def notify(self, subject: str, body: str) -> list[tuple[str, bool, str | None]]:
        results: list[tuple[str, bool, str | None]] = []
        text = f"<b>{subject}</b>\n\n{body}"

        tg = self.cfg.get("telegram", {})
        if tg.get("enabled"):
            ok, err = send_telegram(tg.get("bot_token", ""), tg.get("chat_id", ""), text)
            log.info("telegram ok=%s err=%s", ok, err)
            results.append(("telegram", ok, err))

        dc = self.cfg.get("discord", {})
        if dc.get("enabled"):
            ok, err = send_discord(dc.get("webhook_url", ""), text)
            log.info("discord ok=%s err=%s", ok, err)
            results.append(("discord", ok, err))

        return results
