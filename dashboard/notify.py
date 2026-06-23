"""Optional Telegram alerts for the Bot Command Center.

Enabled only when CC_TELEGRAM_BOT_TOKEN and CC_TELEGRAM_CHAT_ID are set (see
.env.example). Fires when a bot enters down/stale, or recovers to up from
down/stale. No-ops silently otherwise, so it's safe to leave wired in.
"""
from __future__ import annotations

import logging
import os

import httpx

LOG = logging.getLogger("command-center.notify")

_ALERTABLE = {"down", "stale"}
_EMOJI = {"down": "🔴", "stale": "🟠", "up": "✅", "unknown": "⚪"}


def enabled() -> bool:
    return bool(os.getenv("CC_TELEGRAM_BOT_TOKEN") and os.getenv("CC_TELEGRAM_CHAT_ID"))


def _notable(new: str, prev: str) -> bool:
    """Alert on going bad, or on recovering from bad — skip unknown↔up churn."""
    return new in _ALERTABLE or (new == "up" and prev in _ALERTABLE)


async def transition(bot: dict, prev: str) -> None:
    new = bot.get("status", "unknown")
    if not enabled() or not _notable(new, prev):
        return
    text = (
        f"{_EMOJI.get(new, '•')} <b>{bot.get('name', bot.get('id'))}</b> → {new.upper()}\n"
        f"{bot.get('detail', '')}\n"
        f"<i>was {prev} · host {bot.get('host', '?')}</i>"
    )
    await _send(text)


async def _send(text: str) -> None:
    token = os.getenv("CC_TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("CC_TELEGRAM_CHAT_ID", "")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
            )
    except Exception as e:  # noqa: BLE001 — telemetry must never crash the dashboard
        LOG.warning("telegram notify failed: %s", e)
