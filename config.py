"""
Velocity-Z v2 — configuration.
Secrets come from Railway environment variables (never bake keys into code).
"""

import os

from dotenv import load_dotenv

load_dotenv()

# ── API / environment ─────────────────────────────────────────────────
API_KEY    = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TESTNET    = os.getenv("TESTNET", "true").lower() == "true"

# ── Market ────────────────────────────────────────────────────────────
SYMBOL      = os.getenv("SYMBOL", "ETHUSDT")  # Bybit USDT linear perpetual
CATEGORY    = "linear"
TIMEFRAME   = "60"    # 1h bars (Bybit kline interval)
CANDLE_LIMIT = 400    # default fetch size; velocity_bot overrides to 400
POLL_SECONDS = 60     # main loop interval

# ── Risk ──────────────────────────────────────────────────────────────
LEVERAGE = 3          # account leverage cap (risk-based sizing keeps actual exposure low)
