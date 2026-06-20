"""Main async loop — wakes on 30m bar close, evaluates signal, executes."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from ccxt.base.errors import AuthenticationError, PermissionDenied

from bot.db import close_trade, fetch_daily_stats, fetch_trade_stats, init_db, open_trade, snapshot_equity
from bot.exchange import BybitConnector
from bot.strategy import AvaxSpectralStrategy
from bot import telegram

LOG = logging.getLogger("bot8.runner")

# How many seconds past bar close to poll (gives exchange time to settle).
BAR_DELAY_SECS = 10
BAR_SECONDS = 30 * 60  # 30-minute bars

# If the just-closed bar isn't on the exchange yet (lag), retry within the
# window rather than waiting a whole bar. 8 x 20s = up to ~160s of cushion.
FRESH_RETRIES = 8
FRESH_RETRY_DELAY = 20


def _next_bar_close(now: datetime) -> float:
    """Seconds until next 30m bar close + delay."""
    ts = now.timestamp()
    remainder = ts % BAR_SECONDS
    return (BAR_SECONDS - remainder) + BAR_DELAY_SECS


def _expected_closed_open(now: datetime) -> "pd.Timestamp":
    """Open-time of the bar that should have just closed at `now`.

    At 12:30:10 the just-closed 30m bar opened at 12:00 — that's the row we
    expect to be the last closed bar.
    """
    import pandas as pd
    last_boundary = (now.timestamp() // BAR_SECONDS) * BAR_SECONDS
    return pd.Timestamp(last_boundary - BAR_SECONDS, unit="s", tz="UTC")


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
        try:
            await self._ex.connect()
        except AuthenticationError as e:
            LOG.error("auth_failed_on_start", extra={"error": str(e)})
            await telegram.send(
                "🛑 <b>Bot 8 — cannot start</b>\n"
                "Bybit rejected your API key/secret (error 10004, signature).\n"
                "Fix <b>BYBIT_API_SECRET</b> in Railway (wrong value or stray "
                "space/newline) and redeploy. No trades will run until this is fixed."
            )
            # Throttle the Railway crash-restart loop so you don't get spammed.
            await asyncio.sleep(600)
            raise
        except PermissionDenied as e:
            LOG.error("permission_failed_on_start", extra={"error": str(e)})
            await telegram.send(
                "🛑 <b>Bot 8 — can't trade</b>\n"
                "Reads work, but your API key can't place orders (Read-Only / no "
                "trade permission). In Bybit give the key <b>Unified Trading - Trade</b> "
                "with Read-Write, then redeploy. It will signal but never fill until fixed."
            )
            await asyncio.sleep(600)
            raise
        await self._reconcile_state()
        self._running = True
        LOG.info("bot8_started")
        await telegram.send("🚀 <b>Bot 8 — AVAX Spectral running</b>\nConnected to Bybit | AVAX/USDT Perp | 3x | 30m bars")
        await self._loop()

    async def _reconcile_state(self) -> None:
        """Sync in-memory state to the real exchange position on startup.

        The bot's position/streak live in RAM and are lost on every Railway
        redeploy, but the live Bybit position (and its SL/TP) survive. Without
        this, after a restart the bot thinks it's flat while a position is open —
        so it can't detect that position closing, and its accounting desyncs.
        """
        try:
            pos = await self._ex.fetch_position()
        except Exception:
            LOG.exception("reconcile_failed")  # non-fatal; first tick will refetch
            return

        if pos.side in ("long", "short") and pos.qty > 0:
            self._strat.state.position = pos.side
            self._strat.state.entry_price = pos.entry_price
            self._strat.state.entry_qty = pos.qty
            # Re-open a DB row so a later SL/TP close is still recorded + alerted.
            self._open_trade_id = open_trade(
                self._db, self._ex.symbol, pos.side, pos.qty, pos.entry_price
            )
            LOG.info(
                "reconciled_open_position",
                extra={"side": pos.side, "qty": pos.qty, "entry": pos.entry_price},
            )
            await telegram.send(
                f"♻️ <b>Bot 8 — resumed after restart</b>\n"
                f"Picked up your open {pos.side.upper()} {pos.qty} AVAX "
                f"@ ${pos.entry_price:.4f} — now tracking it again."
            )
        else:
            self._strat.state.position = "flat"
            self._strat.state.entry_price = 0.0
            self._strat.state.entry_qty = 0.0
            LOG.info("reconciled_flat")

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
            except (AuthenticationError, PermissionDenied) as e:
                LOG.error("auth_error_stopping", extra={"error": str(e)})
                await telegram.send(
                    "🛑 <b>Bot 8 — stopped</b>\n"
                    "Bybit rejected an order mid-run. Reads were working, so the most "
                    "likely cause is the API key being Read-Only or lacking trade "
                    "permission. Check the key in Bybit (Unified Trading - Trade, "
                    "Read-Write), then redeploy."
                )
                await self.stop()
                return
            except Exception:
                LOG.exception("tick_error")
                await asyncio.sleep(60)

    async def _fetch_fresh_ohlcv(self):
        """Fetch closed bars, making sure the just-closed bar is actually present.

        Returns (df, is_fresh). Retries within the bar window if the exchange is
        lagging, so a few seconds of delay never costs us a frame.
        """
        expected = _expected_closed_open(datetime.now(timezone.utc))
        df = await self._ex.fetch_ohlcv()
        for attempt in range(FRESH_RETRIES):
            if len(df) and df.index[-1] >= expected:
                return df, True
            LOG.warning(
                "stale_bar_retry",
                extra={
                    "have": str(df.index[-1]) if len(df) else "none",
                    "want": str(expected),
                    "attempt": attempt + 1,
                },
            )
            await asyncio.sleep(FRESH_RETRY_DELAY)
            df = await self._ex.fetch_ohlcv()
        return df, bool(len(df) and df.index[-1] >= expected)

    async def _tick(self) -> None:
        df, fresh = await self._fetch_fresh_ohlcv()
        if not fresh:
            have = str(df.index[-1]) if len(df) else "none"
            LOG.error("stale_bar_giving_up", extra={"have": have})
            await telegram.send(
                "⚠️ <b>Bot 8 — frame skipped</b>\n"
                "Bybit didn't return the just-closed 30m bar in time. "
                "No trade this bar; will re-check at the next close."
            )
            return
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

        await telegram.notify_tick(signal.action, signal.centroid, signal.is_trending, balance, position.side)
        snapshot_equity(self._db, balance)

        # Check if existing position hit SL/TP (position closed by exchange)
        if self._open_trade_id and position.side == "none" and self._strat.state.position != "flat":
            # Use real fill price from Bybit trade history, fall back to bar close
            real_fill = await self._ex.fetch_last_fill_price()
            exit_price = real_fill if real_fill is not None else float(df["close"].iloc[-1])
            entry = self._strat.state.entry_price
            side = self._strat.state.position
            qty = self._strat.state.entry_qty
            pnl = (exit_price - entry) * qty if side == "long" else (entry - exit_price) * qty
            close_trade(self._db, self._open_trade_id, exit_price, pnl)
            self._strat.state.on_close(pnl)
            self._open_trade_id = None
            LOG.info("trade_closed_by_exchange", extra={"pnl": round(pnl, 2), "fill": exit_price})
            await telegram.notify_close(side, pnl)

        # Daily summary — fires once per day at first bar on or after 09:00 UTC
        now_utc = datetime.now(timezone.utc)
        if now_utc.hour >= 9 and now_utc.day != self._last_summary_day:
            self._last_summary_day = now_utc.day
            today_str = now_utc.strftime("%Y-%m-%d")
            daily = fetch_daily_stats(self._db, today_str)
            stats = fetch_trade_stats(self._db)
            await telegram.notify_daily_summary(
                balance=balance,
                position=position.side,
                today_trades=daily["total"],
                today_pnl=daily["pnl"],
                total_trades=stats.get("total", 0),
                win_rate=stats.get("win_rate", 0.0),
                total_pnl=stats.get("total_pnl", 0.0),
            )

        if signal.action == "hold":
            return

        # Time exit: close current position, don't open a new one
        if signal.action == "time_exit":
            if position.side != "none" and self._open_trade_id:
                await self._ex.close_position(position.side, position.qty)
                real_fill = await self._ex.fetch_last_fill_price()
                exit_price = real_fill if real_fill is not None else signal.price
                entry = self._strat.state.entry_price
                side = self._strat.state.position
                qty = position.qty
                pnl = (exit_price - entry) * qty if side == "long" else (entry - exit_price) * qty
                close_trade(self._db, self._open_trade_id, exit_price, pnl)
                self._strat.state.on_close(pnl)
                self._open_trade_id = None
                LOG.info("time_exit_fired", extra={"bars_held": self._strat.time_exit_bars, "pnl": round(pnl, 2)})
                await telegram.notify_close(side, pnl)
                await telegram.send(
                    f"⏱ <b>Time Exit — Bot 8</b>\n"
                    f"Closed {side.upper()} after {self._strat.time_exit_bars} bars (12h).\n"
                    f"P&L: {'+'if pnl>=0 else ''}{pnl:.2f} USDT"
                )
            return

        # Close existing position first if flipping direction
        if position.side != "none":
            await self._ex.close_position(position.side, position.qty)
            if self._open_trade_id:
                real_fill = await self._ex.fetch_last_fill_price()
                exit_price = real_fill if real_fill is not None else signal.price
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
            self._strat.state.entry_qty = signal.qty
            self._strat.state.on_open()
            if self._strat.state.win_streak >= self._strat.am_stk_min:
                self._strat.state.consec_3x += 1
            await telegram.notify_trade("long", signal.qty, signal.price, signal.sl_price, signal.tp_price)

        elif signal.action in ("short", "sell"):
            await self._ex.enter_short(signal.qty, signal.sl_price, signal.tp_price)
            self._open_trade_id = open_trade(
                self._db, self._ex.symbol, "short", signal.qty, signal.price
            )
            self._strat.state.position = "short"
            self._strat.state.entry_price = signal.price
            self._strat.state.entry_qty = signal.qty
            self._strat.state.on_open()
            if self._strat.state.win_streak >= self._strat.am_stk_min:
                self._strat.state.consec_3x += 1
            await telegram.notify_trade("short", signal.qty, signal.price, signal.sl_price, signal.tp_price)
