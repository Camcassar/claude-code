"""
Velocity-Z v2 Bot — ETHUSDT 1h momentum.

Theory: reverse-engineered from Hilbert F36 (136 verified trades, PF 2.28, 2.4yr).
Signal: EMA-8 velocity z-score |z|>=2.5 + trend alignment filter.
Exit:   fixed brackets TP +6.5% / SL -3.0% (server-side on Bybit).
Risk:   2% of equity per trade (~67% notional at 3% SL).

Optimised params (108-combo sweep, best Sharpe 2.62):
  TREND_BARS = 200   (was 400)
  TP_PCT     = 0.065 (was 0.055)
  SL_PCT     = 0.030 (was 0.025)

Run: python velocity_bot.py
"""

import logging
import sys
import time
from datetime import datetime, timezone
from math import sqrt, floor

import config
from exchange import Bybit
from indicators import ema as ema_series

# ── strategy parameters (optimised; change only with a new backtest) ──
SYMBOL      = "ETHUSDT"
INTERVAL    = "60"       # 1h bars
EMA_N       = 8
VEL_LAG     = 3
Z_WIN       = 144
Z_THR       = 2.5
TREND_BARS  = 200        # optimised from 400
TP_PCT      = 0.065      # optimised from 0.055
SL_PCT      = 0.030      # optimised from 0.025
RISK_PCT    = 2.0        # % equity risked per trade
POLL_SECONDS = 60
CANDLES_NEEDED = TREND_BARS + Z_WIN + VEL_LAG + 10  # 357 -> fetch 400
CB_LOSSES   = 4          # circuit breaker: pause after 4 straight losses
CB_PAUSE_BARS = 72       # ... for 72 bars (3 days)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s velocity: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("velocity_bot.log"),
    ],
)
log = logging.getLogger("velocity")


def compute_signal(candles):
    """Returns +1 (long), -1 (short), or 0 for the most recent CLOSED candle."""
    closes = [c["close"] for c in candles]
    if len(closes) < CANDLES_NEEDED:
        return 0

    base = ema_series(closes, EMA_N)
    vel = [
        (base[i] - base[i - VEL_LAG]) / base[i - VEL_LAG]
        for i in range(VEL_LAG, len(base))
    ]
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
        log.info("signal z=%.2f blocked by %dh trend filter", z, TREND_BARS)
        return 0

    log.info("SIGNAL %s | z=%.2f trend=%+.2f",
             "LONG" if side == 1 else "SHORT", z, trend)
    return side


class VelocityBot:
    def __init__(self):
        self.ex = Bybit(symbol=SYMBOL)
        self.last_bar = 0
        self.loss_streak = 0
        self.pause_until_bar = 0
        self.had_position = False
        self.last_pnl_ts = int(time.time() * 1000)

    def _track_results(self):
        """Update loss streak from closed PnL (drives circuit breaker)."""
        try:
            for rec in sorted(self.ex.get_closed_pnl(), key=lambda r: r["ts"]):
                if rec["ts"] > self.last_pnl_ts:
                    self.last_pnl_ts = rec["ts"]
                    if rec["pnl"] < 0:
                        self.loss_streak += 1
                        log.info("loss booked (%.2f). streak=%d",
                                 rec["pnl"], self.loss_streak)
                        if self.loss_streak >= CB_LOSSES:
                            self.pause_until_bar = (
                                self.last_bar + CB_PAUSE_BARS * 3_600_000
                            )
                            log.warning(
                                "CIRCUIT BREAKER: %d straight losses — pausing 3 days",
                                self.loss_streak,
                            )
                    else:
                        self.loss_streak = 0
        except Exception as e:
            log.warning("pnl tracking failed: %s", e)

    def tick(self):
        candles = self.ex.get_candles(limit=400)
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
        if pos:
            self.had_position = True
            return  # brackets manage the exit

        if self.had_position:
            log.info("position closed by bracket.")
            self.had_position = False

        if bar_ts < self.pause_until_bar:
            log.info("circuit breaker active — skipping bar")
            return

        side = compute_signal(closed)
        if side == 0:
            return

        equity = self.ex.get_equity()
        px = closed[-1]["close"]
        qty_step, min_qty = self.ex.get_instrument_limits()
        qty = (RISK_PCT / 100 * equity) / (SL_PCT * px)
        qty = floor(round(qty / qty_step, 9)) * qty_step

        if qty < min_qty:
            log.warning("qty %.4f below minimum %.4f, skipping", qty, min_qty)
            return

        order_side = "Buy" if side == 1 else "Sell"
        tp = px * (1 + side * TP_PCT)
        sl = px * (1 - side * SL_PCT)

        log.info(
            "ENTER %s %.4f %s @ ~%.2f | TP %.2f SL %.2f | equity %.2f",
            order_side, qty, SYMBOL, px, tp, sl, equity,
        )
        self.ex.market_order(order_side, qty, stop_loss=sl, take_profit=tp)
        self.had_position = True

    def run(self):
        log.info(
            "Velocity-Z v2 starting | %s 1h | TP %.1f%% SL %.1f%% trend=%dh | testnet=%s",
            SYMBOL, TP_PCT * 100, SL_PCT * 100, TREND_BARS, config.TESTNET,
        )
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
