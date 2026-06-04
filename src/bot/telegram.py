"""Telegram notifications for Bot 8 — AVAX Spectral."""
from __future__ import annotations

import asyncio
import logging
import requests

LOG = logging.getLogger("bot8.telegram")

_TOKEN = ""
_CHAT_ID = ""


def init(token: str, chat_id: str) -> None:
    global _TOKEN, _CHAT_ID
    _TOKEN = token
    _CHAT_ID = chat_id
    if token and chat_id:
        # Log a masked fingerprint so you can confirm THIS bot is posting to the
        # dedicated AVAX chat — and not sharing a token/chat with another bot.
        bot_id = token.split(":", 1)[0]
        LOG.info("telegram_target", extra={"bot_id": bot_id, "chat_id": chat_id})
    else:
        LOG.info("telegram_disabled")


def _post(text: str) -> None:
    """Blocking HTTP call — must be run in a thread executor."""
    requests.post(
        f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
        json={"chat_id": _CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=6,
    )


async def send(text: str) -> None:
    if not _TOKEN or not _CHAT_ID:
        return
    try:
        await asyncio.to_thread(_post, text)
    except Exception as e:
        LOG.warning("telegram_send_failed", extra={"error": str(e)})


async def notify_tick(action: str, centroid: float, trending: bool, balance: float, position: str) -> None:
    trending_str = "✅ Trending" if trending else "〰️ Choppy"
    action_emoji = {
        "buy": "🟢 LONG ENTRY",
        "short": "🔴 SHORT ENTRY",
        "sell": "🔴 EXIT LONG",
        "cover": "🟢 EXIT SHORT",
        "hold": "⏸ Hold",
    }.get(action, action.upper())

    await send(
        f"<b>Bot 8 — AVAX Spectral</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Signal: {action_emoji}\n"
        f"Market: {trending_str} (centroid {centroid:.1f})\n"
        f"Position: {position}\n"
        f"Balance: ${balance:.2f} USDT"
    )


async def notify_trade(side: str, qty: float, price: float, sl: float, tp: float) -> None:
    emoji = "🟢" if side == "long" else "🔴"
    await send(
        f"{emoji} <b>Trade Opened — {side.upper()}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Qty: {qty:.3f} AVAX @ ${price:.4f}\n"
        f"SL: ${sl:.4f} (-2.5%)\n"
        f"TP: ${tp:.4f} (+7.0%)"
    )


async def notify_daily_summary(
    balance: float,
    position: str,
    today_trades: int,
    today_pnl: float,
    total_trades: int,
    win_rate: float,
    total_pnl: float,
) -> None:
    from datetime import datetime, timezone
    date_str = datetime.now(timezone.utc).strftime("%a %d %b %Y")
    today_pnl_str = f"{'+'if today_pnl>=0 else ''}{today_pnl:.2f}"
    total_pnl_str = f"{'+'if total_pnl>=0 else ''}{total_pnl:.2f}"
    await send(
        f"<b>Daily Summary — Bot 8 AVAX</b>\n"
        f"📅 {date_str}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Balance: ${balance:.2f} USDT\n"
        f"Position: {position.upper()}\n"
        f"Today: {today_trades} trade(s) | P&L {today_pnl_str} USDT\n"
        f"All-time: {total_trades} trades | {win_rate:.1f}% win rate\n"
        f"Total P&L: {total_pnl_str} USDT"
    )


async def notify_close(side: str, pnl: float) -> None:
    emoji = "✅" if pnl >= 0 else "❌"
    await send(
        f"{emoji} <b>Trade Closed</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Side: {side.upper()}\n"
        f"P&L: {'+'if pnl>=0 else ''}{pnl:.2f} USDT"
    )
