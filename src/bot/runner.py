"""Main async loop — wakes on 30m bar close, evaluates signal, executes."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from bot.db import close_trade, fetch_daily_stats, fetch_trade_stats, init_db, open_trade, snapshot_equity
from bot.exchange import BybitConnector
from bot.strategy import AvaxSpectralStrategy
from bot import telegram

LOG = logging.getLogger("bot8.runner")

# How many seconds past bar close to poll (gives exchange time to settle).
BAR_DELAY_SECS = 10
BAR_SECONDS = 30 * 60  # 30-minute bars


def _next_bar_close(now: datetime) -> float:
    """Seconds until next 30m bar close + delay."""
    ts = now.timestamp()
    remainder = ts % BAR_SECONDS
    return (BAR_SECONDS - remainder) + BAR_DELAY_SECS


class Bot8Runner:
    def __init__(
        self,
        exchange: BybitConnector,
        strategy: AvaxSpectralStrategy,
        db_path: Path,
        kill_switch: Path,
    ) -> None:
        self._ex = exchange
        self._strat = strategy
        self._db = db_path
        self._kill = kill_switch
        self._open_trade_id: int | None = None
        self._running = False
        self._last_summary_day: int = -1

    async def start(self) -> None:
        init_db(self._db)
        await self._ex.connect()
        self._running = True
        LOG.info("bot8_started")
        await self._loop()

    async def stop(self) -> None:
        self._running = False
        await self._ex.close()
        LOG.info("bot8_stopped")

    async def _loop(self) -> None:
        while self._running:
            if self._kill.exists():
                LOG.warning("kill_switch_active_stopping")
                await self.stop()
                return

            wait = _next_bar_close(datetime.now(timezone.utc))
            LOG.info("waiting_for_bar", extra={"seconds": round(wait)})
            await asyncio.sleep(wait)

            try:
                await self._tick()
            except Exception:
                LOG.exception("tick_error")
                await asyncio.sleep(60)

    async def _tick(self) -> None:
        df = await self._ex.fetch_ohlcv()
        position = await self._ex.fetch_position()
        balance = await self._ex.fetch_balance()
        signal = self._strat.evaluate(df, equity=balance)

        LOG.info(
            "tick",
            extra={
                "action": signal.action,
                "centroid": round(signal.centroid, 2),
                "trending": signal.is_trending,
                "position": position.side,
                "balance_usdt": round(balance, 2),
            },
        )

        telegram.notify_tick(signal.action, signal.centroid, signal.is_trending, balance, position.side)
        snapshot_equity(self._db, balance)

        # Check if existing position hit SL/TP (position closed by exchange)
        if self._open_trade_id and position.side == "none" and self._strat.state.position != "flat":
            exit_price = float(df["close"].iloc[-1])
            entry = self._strat.state.entry_price
            side = self._strat.state.position
            qty = signal.qty
            pnl = (exit_price - entry) * qty if side == "long" else (entry - exit_price) * qty
            close_trade(self._db, self._open_trade_id, exit_price, pnl)
            self._strat.state.on_close(pnl)
            self._open_trade_id = None
            LOG.info("trade_closed_by_exchange", extra={"pnl": round(pnl, 2)})
            telegram.notify_close(side, pnl)

        if signal.action == "hold":
            return

        # Close existing position first if flipping
        if position.side != "none":
            await self._ex.close_position(position.side)
            if self._open_trade_id:
                exit_price = signal.price
                entry = self._strat.state.entry_price
                side = self._strat.state.position
                qty = position.qty
                pnl = (exit_price - entry) * qty if side == "long" else (entry - exit_price) * qty
                close_trade(self._db, self._open_trade_id, exit_price, pnl)
                self._strat.state.on_close(pnl)
                self._open_trade_id = None

        # Enter new position
        if signal.action in ("buy", "cover"):
            await self._ex.enter_long(signal.qty, signal.sl_price, signal.tp_price)
            self._open_trade_id = open_trade(
                self._db, self._ex.symbol, "long", signal.qty, signal.price
            )
            self._strat.state.position = "long"
            self._strat.state.entry_price = signal.price
            if self._strat.state.win_streak >= self._strat.am_stk_min:
                self._strat.state.consec_3x += 1
            telegram.notify_trade("long", signal.qty, signal.price, signal.sl_price, signal.tp_price)

        elif signal.action in ("short", "sell"):
            await self._ex.enter_short(signal.qty, signal.sl_price, signal.tp_price)
            self._open_trade_id = open_trade(
                self._db, self._ex.symbol, "short", signal.qty, signal.price
            )
            self._strat.state.position = "short"
            self._strat.state.entry_price = signal.price
            if self._strat.state.win_streak >= self._strat.am_stk_min:
                self._strat.state.consec_3x += 1
            telegram.notify_trade("short", signal.qty, signal.price, signal.sl_price, signal.tp_price)

        # Daily summary — fires once per day at first bar on or after 09:00 UTC
        now_utc = datetime.now(timezone.utc)
        if now_utc.hour >= 9 and now_utc.day != self._last_summary_day:
            self._last_summary_day = now_utc.day
            today_str = now_utc.strftime("%Y-%m-%d")
            daily = fetch_daily_stats(self._db, today_str)
            stats = fetch_trade_stats(self._db)
            telegram.notify_daily_summary(
                balance=balance,
                position=position.side,
                today_trades=daily["total"],
                today_pnl=daily["pnl"],
                total_trades=stats.get("total", 0),
                win_rate=stats.get("win_rate", 0.0),
                total_pnl=stats.get("total_pnl", 0.0),
            )
