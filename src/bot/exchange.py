"""Bybit connector via ccxt (live)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import ccxt.async_support as ccxt  # type: ignore[import-untyped]
import pandas as pd

LOG = logging.getLogger("bot8.exchange")


@dataclass
class Position:
    side: str        # "long" | "short" | "none"
    qty: float
    entry_price: float
    unrealised_pnl: float


class BybitConnector:
    def __init__(self, api_key: str, api_secret: str, symbol: str = "AVAX/USDT") -> None:
        self.symbol = symbol
        self._ex = ccxt.bybit({
            "apiKey": api_key,
            "secret": api_secret,
            "options": {"defaultType": "linear"},
            "enableRateLimit": True,
            "adjustForTimeDifference": True,
        })

    async def connect(self) -> None:
        await self._ex.load_markets()
        LOG.info("bybit_connected", extra={"symbol": self.symbol})

    async def close(self) -> None:
        await self._ex.close()

    async def fetch_ohlcv(self, timeframe: str = "30m", limit: int = 350) -> pd.DataFrame:
        raw = await self._ex.fetch_ohlcv(self.symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp")
        return df.iloc[:-1]  # drop in-progress bar

    async def fetch_balance(self) -> float:
        bal = await self._ex.fetch_balance({"type": "unified"})
        return float(bal.get("USDT", {}).get("free", 0.0))

    async def fetch_position(self) -> Position:
        positions = await self._ex.fetch_positions([self.symbol])
        for p in positions:
            if p["symbol"] == self.symbol and float(p.get("contracts", 0) or 0) > 0:
                return Position(
                    side=p["side"].lower(),
                    qty=float(p["contracts"]),
                    entry_price=float(p["entryPrice"] or 0),
                    unrealised_pnl=float(p["unrealizedPnl"] or 0),
                )
        return Position(side="none", qty=0.0, entry_price=0.0, unrealised_pnl=0.0)

    async def close_position(self, side: str) -> dict:
        close_side = "sell" if side == "long" else "buy"
        order = await self._ex.create_order(
            symbol=self.symbol,
            type="market",
            side=close_side,
            amount=0,
            params={"reduceOnly": True, "closeOnTrigger": True},
        )
        LOG.info("position_closed", extra={"side": side, "order": order.get("id")})
        return order

    async def enter_long(self, qty: float, sl: float, tp: float) -> dict:
        order = await self._ex.create_order(
            symbol=self.symbol,
            type="market",
            side="buy",
            amount=qty,
            params={
                "stopLoss": {"triggerPrice": sl, "type": "market"},
                "takeProfit": {"triggerPrice": tp, "type": "market"},
            },
        )
        LOG.info("entered_long", extra={"qty": qty, "sl": sl, "tp": tp, "order": order.get("id")})
        return order

    async def enter_short(self, qty: float, sl: float, tp: float) -> dict:
        order = await self._ex.create_order(
            symbol=self.symbol,
            type="market",
            side="sell",
            amount=qty,
            params={
                "stopLoss": {"triggerPrice": sl, "type": "market"},
                "takeProfit": {"triggerPrice": tp, "type": "market"},
            },
        )
        LOG.info("entered_short", extra={"qty": qty, "sl": sl, "tp": tp, "order": order.get("id")})
        return order
