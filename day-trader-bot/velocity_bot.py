"""
Velocity-Z v3 Bot — ETH 1h momentum (improved Hilbert F36 theory).

Theory (reverse-engineered from 136 verified trades of the original):
enter in the direction of an explosive move — z-score of smoothed price
velocity beyond +/-2.5 — and hold with asymmetric brackets (TP 5.5%/SL 2.5%).

v3 fixes over v2 (see scouting/REPORT.md for full analysis):
  1. CRITICAL BUG FIX: v2 never passed its interval to the exchange, so the
     "1h" strategy actually ran on the ORB bot's 5-MINUTE candles — the
     z-window covered 12h instead of 6 days and the 400h trend filter was
     really a 33h filter. None of the backtest evidence applied to what the
     bot traded. v3 passes INTERVAL explicitly.
  2. Persistent state (velocity_state.json): loss streak, circuit-breaker
     pause and PnL cursor survive restarts — v2's breaker was silently
     disarmed by any redeploy, and losses booked while down were never seen.
  3. Same-side cooldown after a loss (trade-list evidence: same-side
     re-entries 6-24h after a loss were the worst bucket, PF 0.60; also a
     sanity guard against Apr-May 2026 style revenge chains).
  4. Slippage-buffered sizing: trade #52 of the original gapped 73% past
     the SL bracket (-4.32% vs -2.58%); we size as if the SL can slip
     SLIP_BUFFER x past its level so worst-case loss stays near RISK_PCT.
  5. Equity floor: stop opening trades below EQUITY_FLOOR x high-water mark
     (pure kill-switch, not curve-fitted).
  6. Optional stop-and-reverse: the original flips when an opposite signal
     fires while in a position (observed once in 136 trades — trade #35
     exits exactly at trade #36's entry). Default on to match the original.
  7. Tick-size-conformant SL/TP prices (v2 rounded to 4dp, which Bybit can
     reject on ETHUSDT whose tick is 0.01).

Carried over from v2 (validated train 2024-25 / OOS 2026):
  400h trend alignment filter, 2%-of-equity risk sizing, server-side
  brackets, circuit breaker after consecutive losses.

Validate any parameter change with:  python backtest_velocity.py
Run:  python velocity_bot.py    (uses same .env as the ORB bot)
"""

import json
import logging
import os
import sys
import time
from math import sqrt, floor

import config
from exchange import Bybit
from indicators import ema as ema_series

# ── strategy parameters (validated; change only with a new backtest) ──
SYMBOL = "ETHUSDT"
INTERVAL = "60"          # 1h bars
EMA_N = 8
VEL_LAG = 3
Z_WIN = 144
Z_THR = 2.5
TREND_BARS = 400
TP_PCT = 0.055
SL_PCT = 0.025
RISK_PCT = 2.0           # % equity risked per trade
SLIP_BUFFER = 1.2        # size as if SL can slip 20% past the bracket
POLL_SECONDS = 60
CANDLES_NEEDED = TREND_BARS + Z_WIN + VEL_LAG + 10   # 557 -> fetch 600
CB_LOSSES = 4            # circuit breaker: pause after 4 straight losses
CB_PAUSE_BARS = 72       # ... for 72 bars (3 days)
COOLDOWN_H = 24          # no same-side re-entry within 24h of a loss
EQUITY_FLOOR = 0.75      # stop trading below 75% of high-water mark
ALLOW_FLIP = True        # close + reverse on opposite signal (as original)
STATE_FILE = "velocity_state.json"

BAR_MS = 3_600_000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s velocity: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler("velocity_bot.log")])
log = logging.getLogger("velocity")


def signal(candles):
    """Returns (+1 long / -1 short / 0) for the most recent CLOSED candle."""
    closes = [c["close"] for c in candles]
    if len(closes) < CANDLES_NEEDED:
        return 0
    base = ema_series(closes, EMA_N)
    vel = [(base[i] - base[i - VEL_LAG]) / base[i - VEL_LAG]
           for i in range(VEL_LAG, len(base))]
    if len(vel) < Z_WIN:
        return 0
    window = vel[-Z_WIN:]
    m = sum(window) / Z_WIN
    var = sum((v - m) ** 2 for v in window) / Z_WIN
    z = (vel[-1] - m) / sqrt(var) if var > 0 else 0.0

    side = 1 if z >= Z_THR else (-1 if z <= -Z_THR else 0)
    if side == 0:
        return 0
    trend = closes[-1] - closes[-1 - TREND_BARS]
    if (side == 1) != (trend > 0):
        log.info("signal z=%.2f blocked by 400h trend filter", z)
        return 0
    log.info("SIGNAL %s | z=%.2f trend=%+.0f", "LONG" if side == 1 else "SHORT",
             z, trend)
    return side


def position_qty(equity, px, qty_step):
    """RISK_PCT of equity against a slippage-buffered stop distance."""
    qty = (RISK_PCT / 100 * equity) / (SL_PCT * SLIP_BUFFER * px)
    return floor(round(qty / qty_step, 9)) * qty_step


class BotState:
    """Persisted risk state — survives restarts so the circuit breaker,
    cooldowns and PnL cursor can't be reset by a redeploy."""

    FIELDS = ("loss_streak", "pause_until_ms", "last_pnl_ts",
              "last_loss_exit", "high_water")

    def __init__(self, path=STATE_FILE):
        self.path = path
        self.loss_streak = 0
        self.pause_until_ms = 0
        self.last_pnl_ts = 0
        self.last_loss_exit = {}      # side ("Buy"/"Sell") -> exit ts ms
        self.high_water = 0.0
        self._load()
        if self.last_pnl_ts == 0:
            # first ever run: only count PnL from now on
            self.last_pnl_ts = int(time.time() * 1000)
            self.save()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path) as f:
                saved = json.load(f)
            for k in self.FIELDS:
                if k in saved:
                    setattr(self, k, saved[k])
        except (json.JSONDecodeError, OSError) as e:
            log.warning("could not load state file: %s", e)

    def save(self):
        with open(self.path, "w") as f:
            json.dump({k: getattr(self, k) for k in self.FIELDS}, f)

    # ── risk bookkeeping ─────────────────────────────────────────
    def book_result(self, rec, now_bar_ms):
        """Book one closed-PnL record. Returns True if it tripped the
        circuit breaker."""
        self.last_pnl_ts = max(self.last_pnl_ts, rec["ts"])
        tripped = False
        if rec["pnl"] < 0:
            self.loss_streak += 1
            self.last_loss_exit[rec["side"]] = rec["ts"]
            if self.loss_streak >= CB_LOSSES:
                self.pause_until_ms = now_bar_ms + CB_PAUSE_BARS * BAR_MS
                self.loss_streak = 0
                tripped = True
        else:
            self.loss_streak = 0
            self.last_loss_exit.pop(rec["side"], None)
        self.save()
        return tripped

    def cooldown_active(self, order_side, now_ms):
        last = self.last_loss_exit.get(order_side)
        return last is not None and now_ms - last < COOLDOWN_H * 3_600_000

    def update_high_water(self, equity):
        if equity > self.high_water:
            self.high_water = equity
            self.save()

    def below_floor(self, equity):
        return self.high_water > 0 and equity < EQUITY_FLOOR * self.high_water


class VelocityBot:
    def __init__(self):
        self.ex = Bybit(symbol=SYMBOL, interval=INTERVAL)
        self.state = BotState()
        self.last_bar = 0
        self.had_position = False

    def _track_results(self):
        """Book closed PnL into persistent state (drives breaker/cooldown)."""
        try:
            for rec in sorted(self.ex.get_closed_pnl(), key=lambda r: r["ts"]):
                if rec["ts"] > self.state.last_pnl_ts:
                    if rec["pnl"] < 0:
                        log.info("loss booked (%.2f %s). streak=%d", rec["pnl"],
                                 rec["side"], self.state.loss_streak + 1)
                    if self.state.book_result(rec, self.last_bar):
                        log.warning("CIRCUIT BREAKER: %d straight losses — "
                                    "pausing %d bars", CB_LOSSES, CB_PAUSE_BARS)
        except Exception as e:
            log.warning("pnl tracking failed: %s", e)

    def _enter(self, side, px):
        equity = self.ex.get_equity()
        self.state.update_high_water(equity)
        if self.state.below_floor(equity):
            log.warning("EQUITY FLOOR: %.2f < %.0f%% of high-water %.2f — "
                        "not trading", equity, EQUITY_FLOOR * 100,
                        self.state.high_water)
            return
        qty_step, min_qty = self.ex.get_instrument_limits()
        qty = position_qty(equity, px, qty_step)
        if qty < min_qty:
            log.warning("qty %.4f below minimum, skipping", qty)
            return
        order_side = "Buy" if side == 1 else "Sell"
        tp = px * (1 + side * TP_PCT)
        sl = px * (1 - side * SL_PCT)
        log.info("ENTER %s %.4f %s @ ~%.2f | TP %.2f SL %.2f | eq %.2f",
                 order_side, qty, SYMBOL, px, tp, sl, equity)
        self.ex.market_order(order_side, qty, stop_loss=sl, take_profit=tp)
        self.had_position = True

    def tick(self):
        candles = self.ex.get_candles(limit=600)
        if len(candles) < CANDLES_NEEDED + 1:
            log.warning("not enough candles yet (%d)", len(candles))
            return
        closed = candles[:-1]
        bar_ts = closed[-1]["ts"]
        if bar_ts == self.last_bar:
            return
        self.last_bar = bar_ts
        self._track_results()

        pos = self.ex.get_position()
        if pos is None and self.had_position:
            log.info("position closed by bracket.")
            self.had_position = False

        if bar_ts < self.state.pause_until_ms:
            return                      # circuit breaker active

        side = signal(closed)

        if pos:
            self.had_position = True
            pos_dir = 1 if pos["side"] == "Buy" else -1
            if ALLOW_FLIP and side not in (0, pos_dir):
                order_side = "Buy" if side == 1 else "Sell"
                if self.state.cooldown_active(order_side, bar_ts + BAR_MS):
                    log.info("opposite signal but %s cooldown active — "
                             "holding position", order_side)
                    return
                log.info("FLIP: opposite signal — closing %s", pos["side"])
                self.ex.close_position(pos["side"], pos["size"])
                self._enter(side, closed[-1]["close"])
            return                      # otherwise brackets manage the exit

        if side == 0:
            return
        order_side = "Buy" if side == 1 else "Sell"
        if self.state.cooldown_active(order_side, bar_ts + BAR_MS):
            log.info("signal %s blocked by %dh same-side cooldown",
                     order_side, COOLDOWN_H)
            return
        self._enter(side, closed[-1]["close"])

    def run(self):
        log.info("Velocity-Z v3 starting | %s %sm bars | testnet=%s",
                 SYMBOL, INTERVAL, config.TESTNET)
        self.ex.set_leverage()
        while True:
            try:
                self.tick()
            except KeyboardInterrupt:
                log.info("stopped by user")
                break
            except Exception as e:
                log.error("tick error: %s", e, exc_info=True)
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    if not config.API_KEY or not config.API_SECRET:
        sys.exit("Set BYBIT_API_KEY and BYBIT_API_SECRET in .env first.")
    VelocityBot().run()
