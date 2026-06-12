"""
Exchange — thin wrapper over Bybit v5 (pybit unified trading).
All API calls live here so the strategy stays testable offline.
"""

import logging
from decimal import Decimal, ROUND_HALF_EVEN

from pybit.unified_trading import HTTP

import config

log = logging.getLogger("exchange")


class Bybit:
    def __init__(self, symbol=None, interval=None):
        self.http = HTTP(
            testnet=config.TESTNET,
            api_key=config.API_KEY,
            api_secret=config.API_SECRET,
        )
        self.symbol = symbol or config.SYMBOL
        # Each bot must pass its own interval. config.TIMEFRAME is the ORB
        # bot's 5m default — the v2 velocity bot forgot to override it and
        # ran its 1h-fitted strategy on 5m candles.
        self.interval = interval or config.TIMEFRAME
        self._qty_step = None
        self._min_qty = None
        self._tick = None

    # ── market data ──────────────────────────────────────────────
    def get_candles(self, limit=None):
        """Closed + current candles, oldest -> newest.
        The last element is the still-forming candle; bot.py drops it."""
        r = self.http.get_kline(
            category=config.CATEGORY,
            symbol=self.symbol,
            interval=self.interval,
            limit=limit or config.CANDLE_LIMIT,
        )
        rows = r["result"]["list"]  # newest first
        candles = [
            {
                "ts": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
            for row in rows
        ]
        candles.reverse()
        return candles

    def get_last_price(self):
        r = self.http.get_tickers(category=config.CATEGORY, symbol=self.symbol)
        return float(r["result"]["list"][0]["lastPrice"])

    def get_instrument_limits(self):
        """(qty_step, min_qty) for order sizing, cached."""
        if self._qty_step is None:
            r = self.http.get_instruments_info(
                category=config.CATEGORY, symbol=self.symbol
            )
            info = r["result"]["list"][0]
            f = info["lotSizeFilter"]
            self._qty_step = float(f["qtyStep"])
            self._min_qty = float(f["minOrderQty"])
            self._tick = Decimal(info["priceFilter"]["tickSize"])
        return self._qty_step, self._min_qty

    def round_price(self, px):
        """Round a price to the instrument tick size (Bybit rejects
        SL/TP prices that don't conform). Returns a string."""
        if self._tick is None:
            self.get_instrument_limits()
        d = (Decimal(str(px)) / self._tick).to_integral_value(ROUND_HALF_EVEN)
        return str(d * self._tick)

    # ── account ──────────────────────────────────────────────────
    def get_equity(self):
        r = self.http.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        coins = r["result"]["list"][0]["coin"]
        for c in coins:
            if c["coin"] == "USDT":
                return float(c["equity"])
        return 0.0

    def get_position(self):
        """Open position dict or None."""
        r = self.http.get_positions(category=config.CATEGORY, symbol=self.symbol)
        for p in r["result"]["list"]:
            if float(p["size"]) > 0:
                return {
                    "side": p["side"],
                    "size": float(p["size"]),
                    "entry": float(p["avgPrice"]),
                    "unrealized_pnl": float(p["unrealisedPnl"]),
                    "stop_loss": float(p["stopLoss"]) if p["stopLoss"] else None,
                }
        return None

    def set_leverage(self):
        try:
            self.http.set_leverage(
                category=config.CATEGORY,
                symbol=self.symbol,
                buyLeverage=str(config.LEVERAGE),
                sellLeverage=str(config.LEVERAGE),
            )
        except Exception as e:  # already set -> Bybit errors; harmless
            if "110043" not in str(e):
                log.warning("set_leverage: %s", e)

    # ── orders ───────────────────────────────────────────────────
    def market_order(self, side, qty, stop_loss, take_profit):
        """Market entry with attached SL/TP (Bybit handles exits server-side,
        so a bot crash can't leave an unprotected position)."""
        return self.http.place_order(
            category=config.CATEGORY,
            symbol=self.symbol,
            side=side,
            orderType="Market",
            qty=str(qty),
            stopLoss=self.round_price(stop_loss),
            takeProfit=self.round_price(take_profit),
            slTriggerBy="LastPrice",
            tpTriggerBy="LastPrice",
        )

    def move_stop(self, new_stop):
        return self.http.set_trading_stop(
            category=config.CATEGORY,
            symbol=self.symbol,
            stopLoss=self.round_price(new_stop),
            slTriggerBy="LastPrice",
            positionIdx=0,
        )

    def close_position(self, side, size):
        """Reduce-only market close. `side` is the position side."""
        opposite = "Sell" if side == "Buy" else "Buy"
        return self.http.place_order(
            category=config.CATEGORY,
            symbol=self.symbol,
            side=opposite,
            orderType="Market",
            qty=str(size),
            reduceOnly=True,
        )

    def get_closed_pnl(self, limit=50):
        """Recent closed-trade PnL records, with the POSITION side
        (Bybit reports the side of the closing order — the opposite)."""
        r = self.http.get_closed_pnl(
            category=config.CATEGORY, symbol=self.symbol, limit=limit
        )
        return [
            {
                "ts": int(row["updatedTime"]),
                "pnl": float(row["closedPnl"]),
                "side": "Sell" if row["side"] == "Buy" else "Buy",
            }
            for row in r["result"]["list"]
        ]
