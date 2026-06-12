"""
Indicators — pure functions over candle lists.

A candle is a dict: {"ts": int_ms, "open": f, "high": f, "low": f,
                     "close": f, "volume": f}
Candles are ordered oldest -> newest.
"""


def ema(values, period):
    """Exponential moving average. Returns list aligned with input."""
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    out = [sum(values[:period]) / period]  # seed with SMA
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def atr(candles, period=14):
    """Average True Range (Wilder). Returns latest ATR value or None."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l = candles[i]["high"], candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


def session_vwap(candles, session_start_ms):
    """Volume-weighted average price anchored at session start."""
    pv = vol = 0.0
    for c in candles:
        if c["ts"] < session_start_ms:
            continue
        typical = (c["high"] + c["low"] + c["close"]) / 3
        pv += typical * c["volume"]
        vol += c["volume"]
    return pv / vol if vol > 0 else None


def avg_volume(candles, lookback=20, exclude_last=True):
    """Average volume of the `lookback` candles before the latest one."""
    pool = candles[:-1] if exclude_last else candles
    if len(pool) < lookback:
        return None
    window = pool[-lookback:]
    return sum(c["volume"] for c in window) / lookback
