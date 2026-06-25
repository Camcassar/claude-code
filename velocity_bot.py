"""
Momentum-Z | ETHUSDT 1H — Cam's live Bybit bot.

Signal: EMA-8 velocity z-score |z|>=2.5 + 200h trend alignment filter.
Exit:   TP +6.5% / SL -3.0% server-side brackets (crash-safe).
Risk:   2% equity per trade. Circuit breaker: 4 losses -> 3-day pause.

Backtest (Jan 2024 – Jun 2026): +364% net, PF 1.92, DD 13.3%, Sharpe 2.62.
"""

import logging
import os
import sys
import time
from math import floor, sqrt

import requests

import config
from exchange import Bybit
from indicators import ema as ema_series

BOT_NAME    = "Momentum-Z | ETH 1H"
SYMBOL      = "ETHUSDT"
EMA_N       = 8
VEL_LAG     = 3
Z_WIN       = 144
Z_THR       = 2.5
TREND_BARS  = 200
TP_PCT      = 0.065
SL_PCT      = 0.030
RISK_PCT    = 2.0
POLL_SECONDS = 60
CANDLES_NEEDED = TREND_BARS + Z_WIN + VEL_LAG + 10
CB_LOSSES   = 4
CB_PAUSE_BARS = 72

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [Momentum-Z]: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("momentum_z")

# ── Telegram ──────────────────────────────────────────────────────────
_TG_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def tg(msg: str) -> None:
    """Fire-and-forget Telegram message. Silently drops on failure."""
    if not _TG_TOKEN or not _TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
            json={"chat_id": _TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=5,
        )
    except Exception:
        pass


# ── Signal ────────────────────────────────────────────────────────────
def compute_signal(candles):
    """Returns +1 (long), -1 (short), or 0."""
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
        log.info("z=%.2f blocked by trend filter", z)
        return 0

    log.info("SIGNAL %s | z=%.2f", "LONG" if side == 1 else "SHORT", z)
    return side


# ── Bot ───────────────────────────────────────────────────────────────
class MomentumZBot:
    def __init__(self):
        self.ex = Bybit(symbol=SYMBOL)
        self.last_bar = 0
        self.loss_streak = 0
        self.pause_until_bar = 0
        self.had_position = False
        self.last_pnl_ts = int(time.time() * 1000)

    def _track_results(self):
        try:
            for rec in sorted(self.ex.get_closed_pnl(), key=lambda r: r["ts"]):
                if rec["ts"] > self.last_pnl_ts:
                    self.last_pnl_ts = rec["ts"]
                    pnl = rec["pnl"]
                    if pnl < 0:
                        self.loss_streak += 1
                        log.info("loss booked %.2f USDT | streak=%d", pnl, self.loss_streak)
                        tg(f"❌ *{BOT_NAME}*\nTrade closed: `{pnl:+.2f} USDT`\nLoss streak: {self.loss_streak}")
                        if self.loss_streak >= CB_LOSSES:
                            self.pause_until_bar = self.last_bar + CB_PAUSE_BARS * 3_600_000
                            msg = f"⚠️ *{BOT_NAME}* — CIRCUIT BREAKER\n{CB_LOSSES} straight losses. Pausing 3 days."
                            log.warning(msg)
                            tg(msg)
                    else:
                        self.loss_streak = 0
                        tg(f"✅ *{BOT_NAME}*\nTrade closed: `+{pnl:.2f} USDT`")
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
            return

        if self.had_position:
            log.info("position closed by bracket")
            self.had_position = False

        if bar_ts < self.pause_until_bar:
            log.info("circuit breaker active — skipping")
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
            log.warning("qty %.4f below minimum, skipping", qty)
            return

        direction = "LONG" if side == 1 else "SHORT"
        tp = px * (1 + side * TP_PCT)
        sl = px * (1 - side * SL_PCT)

        log.info("ENTER %s %.4f @ ~%.2f | TP %.2f SL %.2f | equity %.2f",
                 direction, qty, px, tp, sl, equity)

        self.ex.market_order("Buy" if side == 1 else "Sell", qty,
                             stop_loss=sl, take_profit=tp)
        self.had_position = True

        tg(
            f"{'📈' if side == 1 else '📉'} *{BOT_NAME}*\n"
            f"*{direction}* {qty} {SYMBOL}\n"
            f"Entry: `{px:.2f}` | TP: `{tp:.2f}` | SL: `{sl:.2f}`\n"
            f"Risk: 2% of `{equity:.2f} USDT`"
        )

    def run(self):
        startup = (
            f"🚀 *{BOT_NAME}* — LIVE\n"
            f"Symbol: {SYMBOL} | TF: 1H\n"
            f"TP: {TP_PCT*100:.1f}% | SL: {SL_PCT*100:.1f}% | Trend: {TREND_BARS}h\n"
            f"Testnet: {config.TESTNET}"
        )
        log.info(startup.replace("*", "").replace("`", ""))
        tg(startup)
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
    MomentumZBot().run()
