"""
Day Trader Bot — configuration.
Every tunable lives here. Secrets come from environment variables (.env).
"""

import os

from dotenv import load_dotenv

load_dotenv()

# ── API / environment ────────────────────────────────────────────────
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TESTNET = os.getenv("TESTNET", "true").lower() == "true"

# ── Market ───────────────────────────────────────────────────────────
SYMBOL = os.getenv("SYMBOL", "SOLUSDT")   # Bybit USDT perpetual
CATEGORY = "linear"
TIMEFRAME = "5"                            # minutes (Bybit kline interval)
CANDLE_LIMIT = 200                         # candles fetched per poll
POLL_SECONDS = 30                          # main loop interval

# ── Sessions (UTC) ───────────────────────────────────────────────────
# Each session: opening range forms in the first OPENING_RANGE_MINUTES,
# trades allowed until session_start + SESSION_WINDOW_HOURS, then any
# open position is flattened (day traders don't hold).
SESSIONS = [
    {"name": "UTC_OPEN", "hour": 0, "minute": 0},
    {"name": "US_OPEN", "hour": 13, "minute": 30},
]
OPENING_RANGE_MINUTES = 30
SESSION_WINDOW_HOURS = 6

# ── Entry filters ────────────────────────────────────────────────────
# Defaults below were the only combo positive in BOTH halves of a 90-day
# SOL walk-forward backtest (Jun 2026). Re-run backtest.py before going live.
VOLUME_MULT = 2.0        # breakout candle volume >= 2x 20-candle avg
VOLUME_LOOKBACK = 20
ATR_PERIOD = 14
RANGE_MIN_ATR = 0.5      # skip session if range narrower than 0.5x ATR
RANGE_MAX_ATR = 3.0      # skip session if range wider than 3x ATR (news spike)

# ── Risk management ──────────────────────────────────────────────────
RISK_PCT = 1.0           # % of equity risked per trade
TP_R_MULT = 3.0          # take profit at 3R
BREAKEVEN_AT_R = 1.0     # move stop to entry once trade is +1R
DAILY_LOSS_LIMIT_PCT = 3.0   # stop trading for the day after -3% equity
MAX_TRADES_PER_SESSION_SIDE = 1  # one long + one short attempt max per session
LEVERAGE = 3             # account leverage cap (position sizing is risk-based anyway)

# ── Misc ─────────────────────────────────────────────────────────────
LOG_FILE = os.getenv("LOG_FILE", "day_trader_bot.log")
STATE_FILE = os.getenv("STATE_FILE", "bot_state.json")
