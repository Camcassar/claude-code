"""Bybit connector via ccxt (live)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, TypeVar

import ccxt.async_support as ccxt  # type: ignore[import-untyped]
import pandas as pd

LOG = logging.getLogger("bot8.exchange")

_T = TypeVar("_T")
_RATE_LIMIT_RETRIES = 4
_RATE_LIMIT_BASE_DELAY = 5.0


async def _with_backoff(fn: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    """Retry a ccxt coroutine on RateLimitExceeded with exponential backoff."""
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            return await fn()
        except ccxt.RateLimitExceeded:
            if attempt == _RATE_LIMIT_RETRIES:
                raise
            delay = _RATE_LIMIT_BASE_DELAY * (2 ** attempt)
            LOG.warning("rate_limit_backoff", extra={"attempt": attempt + 1, "delay_s": round(delay, 1)})
            await asyncio.sleep(delay)


class _BybitLinearOnly(ccxt.bybit):
    """bybit subclass that skips geo-blocked endpoints on Railway's US servers.

    Bybit CloudFront blocks /v5/asset/coin/query-info from non-Asian IPs.
    fetch_markets is restricted to linear-only via fetchMarkets.types option.
    """
    async def fetch_currencies(self, params: dict = {}) -> dict:
        return {}


@dataclass
class Position:
    side: str        # "long" | "short" | "none"
    qty: float
    entry_price: float
    unrealised_pnl: float


class BybitConnector:
    def __init__(self, api_key: str, api_secret: str, symbol: str = "AVAX/USDT", leverage: float = 3.0, testnet: bool = False) -> None:
        self.symbol = symbol
        self.leverage = leverage
        self._key = api_key
        self._secret = api_secret
        self._ex = _BybitLinearOnly({
            "apiKey": api_key,
            "secret": api_secret,
            "options": {
                "defaultType": "linear",
                "fetchMarkets": {"types": ["linear"]},
            },
            "enableRateLimit": True,
            "adjustForTimeDifference": True,
        })
        if testnet:
            self._ex.set_sandbox_mode(True)

    async def connect(self) -> None:
        await _with_backoff(lambda: self._ex.load_markets())
        await self.verify_auth()
        await self._set_leverage()
        LOG.info("bybit_connected", extra={"symbol": self.symbol, "leverage": self.leverage})

    async def verify_auth(self) -> None:
        """Authenticated probe so bad API keys fail LOUDLY at startup.

        load_markets/fetch_ohlcv are public and succeed even with bad keys, which
        is exactly why a wrong secret hid for hours: the bot 'connected' but every
        private call (positions, orders) died with Bybit 10004 'error sign'.
        """
        try:
            await _with_backoff(lambda: self._ex.fetch_balance({"type": "unified"}))
        except ccxt.AuthenticationError as e:
            self._log_credential_fingerprint()
            raise ccxt.AuthenticationError(
                "Bybit rejected the API credentials (error 10004 — signature). "
                "The API KEY is recognised but the SECRET doesn't match. See the "
                "credential_fingerprint log line just above: if *_has_ws is true or "
                "secret_len isn't what Bybit shows, the value in Railway is "
                "corrupted (stray quote/space/newline) even if it looks right. "
                f"Bybit said: {e}"
            ) from e

    def _log_credential_fingerprint(self) -> None:
        """Log a SAFE fingerprint of the creds (never the secret itself) so a
        malformed env var (whitespace, quotes) is visible without guessing."""
        k, s = self._key or "", self._secret or ""
        LOG.error(
            "credential_fingerprint",
            extra={
                "key_len": len(k),
                "key_has_ws": k != k.strip(),
                "key_has_quotes": k.startswith(("'", '"')) or k.endswith(("'", '"')),
                "secret_len": len(s),
                "secret_has_ws": s != s.strip(),
                "secret_has_quotes": s.startswith(("'", '"')) or s.endswith(("'", '"')),
            },
        )

    async def _set_leverage(self) -> None:
        """Best-effort: pin leverage so win-streak-sized orders aren't margin-rejected.

        Bybit raises 'leverage not modified' (110043) if it's already set to the
        same value — that's not an error for us, so swallow it.
        """
        try:
            await self._ex.set_leverage(self.leverage, self.symbol)
            LOG.info("leverage_set", extra={"symbol": self.symbol, "leverage": self.leverage})
        except ccxt.BaseError as e:
            LOG.warning("leverage_set_skipped", extra={"error": str(e)})

    async def close(self) -> None:
        await self._ex.close()

    async def fetch_ohlcv(self, timeframe: str = "30m", limit: int = 350) -> pd.DataFrame:
        raw = await _with_backoff(lambda: self._ex.fetch_ohlcv(self.symbol, timeframe=timeframe, limit=limit))
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp")
        # Keep only bars that have FULLY closed. ccxt timestamps are bar-open
        # times, so a bar opened at T closes at T + bar_secs. Selecting by time
        # (not by position) is robust whether or not the exchange has yet
        # produced the in-progress bar — so we never drop the bar we want.
        bar_secs = self._ex.parse_timeframe(timeframe)
        closed = df.index + pd.Timedelta(seconds=bar_secs) <= pd.Timestamp.now(tz="UTC")
        return df[closed]

    async def fetch_balance(self) -> float:
        bal = await _with_backoff(lambda: self._ex.fetch_balance({"type": "unified"}))
        return float(bal.get("USDT", {}).get("free", 0.0))

    async def fetch_position(self) -> Position:
        positions = await _with_backoff(lambda: self._ex.fetch_positions([self.symbol]))
        for p in positions:
            if p["symbol"] == self.symbol and float(p.get("contracts", 0) or 0) > 0:
                return Position(
                    side=p["side"].lower(),
                    qty=float(p["contracts"]),
                    entry_price=float(p["entryPrice"] or 0),
                    unrealised_pnl=float(p["unrealizedPnl"] or 0),
                )
        return Position(side="none", qty=0.0, entry_price=0.0, unrealised_pnl=0.0)

    async def close_position(self, side: str, qty: float) -> dict:
        if qty <= 0:
            LOG.warning("close_position_skipped_zero_qty", extra={"side": side})
            return {}
        close_side = "sell" if side == "long" else "buy"
        order = await _with_backoff(lambda: self._ex.create_order(
            symbol=self.symbol,
            type="market",
            side=close_side,
            amount=qty,
            params={"reduceOnly": True},
        ))
        LOG.info("position_closed", extra={"side": side, "qty": qty, "order": order.get("id")})
        return order

    async def enter_long(self, qty: float, sl: float, tp: float) -> dict:
        order = await _with_backoff(lambda: self._ex.create_order(
            symbol=self.symbol,
            type="market",
            side="buy",
            amount=qty,
            params={
                "stopLoss": {"triggerPrice": sl, "type": "market"},
                "takeProfit": {"triggerPrice": tp, "type": "market"},
            },
        ))
        LOG.info("entered_long", extra={"qty": qty, "sl": sl, "tp": tp, "order": order.get("id")})
        return order

    async def enter_short(self, qty: float, sl: float, tp: float) -> dict:
        order = await _with_backoff(lambda: self._ex.create_order(
            symbol=self.symbol,
            type="market",
            side="sell",
            amount=qty,
            params={
                "stopLoss": {"triggerPrice": sl, "type": "market"},
                "takeProfit": {"triggerPrice": tp, "type": "market"},
            },
        ))
        LOG.info("entered_short", extra={"qty": qty, "sl": sl, "tp": tp, "order": order.get("id")})
        return order
