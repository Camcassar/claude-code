"""
Strategy — Opening Range Breakout with VWAP bias + volume confirmation.

Pure logic, no API calls. bot.py feeds it candles; it returns signals.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import config
from indicators import atr, avg_volume, session_vwap


@dataclass
class OpeningRange:
    session_name: str
    start_ms: int          # session start
    end_ms: int            # end of opening-range window
    close_ms: int          # session close (time-exit deadline)
    high: float
    low: float

    @property
    def width(self):
        return self.high - self.low


@dataclass
class Signal:
    side: str              # "Buy" or "Sell"
    entry: float           # reference price (breakout candle close)
    stop: float
    take_profit: float
    reason: str


def current_session(now_utc):
    """Return (session_dict, session_start_dt) if `now_utc` falls inside an
    active session window, else (None, None)."""
    for sess in config.SESSIONS:
        for day_offset in (0, -1):  # session may have started yesterday (UTC)
            start = now_utc.replace(
                hour=sess["hour"], minute=sess["minute"], second=0, microsecond=0
            ) + timedelta(days=day_offset)
            end = start + timedelta(hours=config.SESSION_WINDOW_HOURS)
            if start <= now_utc < end:
                return sess, start
    return None, None


def build_opening_range(candles, sess, session_start):
    """Compute the opening range once enough candles have closed.
    Returns OpeningRange or None if the range window isn't complete yet."""
    start_ms = int(session_start.timestamp() * 1000)
    end_ms = start_ms + config.OPENING_RANGE_MINUTES * 60_000
    close_ms = start_ms + config.SESSION_WINDOW_HOURS * 3_600_000

    window = [c for c in candles if start_ms <= c["ts"] < end_ms]
    expected = config.OPENING_RANGE_MINUTES // int(config.TIMEFRAME)
    if len(window) < expected:
        return None
    return OpeningRange(
        session_name=sess["name"],
        start_ms=start_ms,
        end_ms=end_ms,
        close_ms=close_ms,
        high=max(c["high"] for c in window),
        low=min(c["low"] for c in window),
    )


def range_is_tradeable(orange, candles):
    """Range sanity filter: skip dead chop and news-spike sessions."""
    a = atr(candles, config.ATR_PERIOD)
    if a is None or a <= 0:
        return False, "ATR unavailable"
    ratio = orange.width / a
    if ratio < config.RANGE_MIN_ATR:
        return False, f"range too narrow ({ratio:.2f}x ATR)"
    if ratio > config.RANGE_MAX_ATR:
        return False, f"range too wide ({ratio:.2f}x ATR)"
    return True, f"range OK ({ratio:.2f}x ATR)"


def check_breakout(candles, orange):
    """Evaluate the most recent CLOSED candle for a confirmed breakout.
    Returns Signal or None."""
    candle = candles[-1]

    # Must be after the opening range, before session close.
    if candle["ts"] < orange.end_ms or candle["ts"] >= orange.close_ms:
        return None

    vwap = session_vwap(candles, orange.start_ms)
    vol_avg = avg_volume(candles, config.VOLUME_LOOKBACK)
    if vwap is None or vol_avg is None:
        return None

    vol_ok = candle["volume"] >= config.VOLUME_MULT * vol_avg
    close = candle["close"]
    r = orange.width

    # Long: close above range high, price above VWAP, volume confirms.
    if close > orange.high and close > vwap and vol_ok:
        stop = orange.low
        risk = close - stop
        return Signal(
            side="Buy",
            entry=close,
            stop=stop,
            take_profit=close + config.TP_R_MULT * risk,
            reason=(f"ORB long: close {close:.4f} > range high {orange.high:.4f}, "
                    f"above VWAP {vwap:.4f}, vol {candle['volume']:.0f} >= "
                    f"{config.VOLUME_MULT}x avg {vol_avg:.0f}"),
        )

    # Short: close below range low, price below VWAP, volume confirms.
    if close < orange.low and close < vwap and vol_ok:
        stop = orange.high
        risk = stop - close
        return Signal(
            side="Sell",
            entry=close,
            stop=stop,
            take_profit=close - config.TP_R_MULT * risk,
            reason=(f"ORB short: close {close:.4f} < range low {orange.low:.4f}, "
                    f"below VWAP {vwap:.4f}, vol {candle['volume']:.0f} >= "
                    f"{config.VOLUME_MULT}x avg {vol_avg:.0f}"),
        )

    return None


def should_move_to_breakeven(side, entry, stop, last_price):
    """True once the trade is BREAKEVEN_AT_R in profit."""
    risk = abs(entry - stop)
    if risk <= 0:
        return False
    if side == "Buy":
        return last_price >= entry + config.BREAKEVEN_AT_R * risk
    return last_price <= entry - config.BREAKEVEN_AT_R * risk


def past_session_close(now_utc, orange):
    return int(now_utc.timestamp() * 1000) >= orange.close_ms
