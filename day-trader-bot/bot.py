"""
Day Trader Bot — main loop.

ORB + VWAP day-trading strategy on Bybit perps.
Polls every POLL_SECONDS, acts on closed 5m candles.

Run:  python bot.py
"""

import logging
import sys
import time
from datetime import datetime, timezone

import config
import strategy
from exchange import Bybit
from risk import DayTracker, position_size

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE),
    ],
)
log = logging.getLogger("bot")


class DayTraderBot:
    def __init__(self):
        self.ex = Bybit()
        self.day = DayTracker()
        self.orange = None            # current OpeningRange
        self.skipped_session = None   # session name skipped by range filter
        self.active_trade = None      # {"side", "entry", "stop", "session"}
        self.breakeven_moved = False
        self.last_candle_ts = 0
        self.last_pnl_ts = int(time.time() * 1000)

    # ── helpers ─────────────────────────────────────────────────
    def _sync_realized_pnl(self):
        """Pull closed-trade PnL since last check into the day tracker."""
        try:
            for rec in self.ex.get_closed_pnl():
                if rec["ts"] > self.last_pnl_ts:
                    self.day.record_pnl(rec["pnl"])
                    log.info("Realized PnL booked: %+.2f USDT", rec["pnl"])
                    self.last_pnl_ts = max(self.last_pnl_ts, rec["ts"])
        except Exception as e:
            log.warning("closed-pnl sync failed: %s", e)

    def _enter(self, signal, sess):
        equity = self.ex.get_equity()
        qty_step, min_qty = self.ex.get_instrument_limits()
        qty = position_size(equity, signal.entry, signal.stop, qty_step, min_qty)
        if qty <= 0:
            log.warning("Signal skipped — size below minimum (equity %.2f)", equity)
            return
        log.info("ENTER %s %s %s @ ~%.4f | SL %.4f TP %.4f | %s",
                 signal.side, qty, config.SYMBOL, signal.entry,
                 signal.stop, signal.take_profit, signal.reason)
        self.ex.market_order(signal.side, qty, signal.stop, signal.take_profit)
        self.day.record_trade(sess["name"], signal.side)
        self.active_trade = {
            "side": signal.side, "entry": signal.entry,
            "stop": signal.stop, "session": sess["name"],
        }
        self.breakeven_moved = False

    def _manage_open_position(self, now):
        pos = self.ex.get_position()
        if pos is None:
            if self.active_trade:
                log.info("Position closed (SL/TP hit).")
                self.active_trade = None
            return

        if not self.active_trade:  # adopted after restart
            self.active_trade = {
                "side": pos["side"], "entry": pos["entry"],
                "stop": pos["stop_loss"] or pos["entry"], "session": "ADOPTED",
            }

        # Breakeven move at +1R
        if not self.breakeven_moved:
            last = self.ex.get_last_price()
            t = self.active_trade
            if strategy.should_move_to_breakeven(t["side"], t["entry"], t["stop"], last):
                log.info("+1R reached — moving stop to breakeven %.4f", t["entry"])
                self.ex.move_stop(t["entry"])
                self.breakeven_moved = True

        # Time exit at session close
        if self.orange and strategy.past_session_close(now, self.orange):
            log.info("Session close — flattening position (day trade over).")
            self.ex.close_position(pos["side"], pos["size"])
            self.active_trade = None

    # ── main loop ───────────────────────────────────────────────
    def run(self):
        log.info("Day Trader Bot starting | %s | %s | testnet=%s",
                 config.SYMBOL, f"{config.TIMEFRAME}m", config.TESTNET)
        self.ex.set_leverage()

        while True:
            try:
                self.tick()
            except KeyboardInterrupt:
                log.info("Stopped by user.")
                break
            except Exception as e:
                log.error("tick error: %s", e, exc_info=True)
            time.sleep(config.POLL_SECONDS)

    def tick(self):
        now = datetime.now(timezone.utc)
        self._sync_realized_pnl()
        self._manage_open_position(now)

        sess, sess_start = strategy.current_session(now)
        if sess is None:
            self.orange = None
            self.skipped_session = None
            return

        candles = self.ex.get_candles()
        if len(candles) < 2:
            return
        candles = candles[:-1]  # drop the still-forming candle
        latest = candles[-1]
        if latest["ts"] == self.last_candle_ts:
            return  # no new closed candle yet
        self.last_candle_ts = latest["ts"]

        # Build / refresh opening range for this session
        if self.orange is None or self.orange.start_ms != int(sess_start.timestamp() * 1000):
            self.orange = strategy.build_opening_range(candles, sess, sess_start)
            self.skipped_session = None
            if self.orange:
                ok, why = strategy.range_is_tradeable(self.orange, candles)
                log.info("[%s] Opening range %.4f–%.4f | %s",
                         sess["name"], self.orange.low, self.orange.high, why)
                if not ok:
                    self.skipped_session = sess["name"]
        if self.orange is None or self.skipped_session == sess["name"]:
            return

        # Gates before looking for an entry
        if self.active_trade is not None:
            return
        equity = self.ex.get_equity()
        if self.day.daily_loss_hit(equity):
            log.warning("Daily loss limit hit — no more trades today.")
            return

        signal = strategy.check_breakout(candles, self.orange)
        if signal is None:
            return
        if self.day.trades_taken(sess["name"], signal.side) >= config.MAX_TRADES_PER_SESSION_SIDE:
            log.info("Max %s trades for %s already taken.", signal.side, sess["name"])
            return

        self._enter(signal, sess)


if __name__ == "__main__":
    if not config.API_KEY or not config.API_SECRET:
        sys.exit("Set BYBIT_API_KEY and BYBIT_API_SECRET in .env first.")
    DayTraderBot().run()
