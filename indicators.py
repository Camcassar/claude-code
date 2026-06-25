"""
Indicators — pure functions over candle lists.

Candle format: {"ts": int_ms, "open": f, "high": f, "low": f, "close": f, "volume": f}
Ordered oldest -> newest.
"""


def ema(values, period):
    """Exponential moving average. Returns list aligned with input."""
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out
