"""
Unit tests — synthetic-candle checks that signals fire exactly when they should.
Run: python test_strategy.py
"""

from datetime import datetime, timezone, timedelta

import config
import strategy
from indicators import atr, avg_volume, ema, session_vwap

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


def mk(ts, o, h, l, c, v):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}


def candles_for_session(start_ms, specs):
    """specs: list of (o,h,l,c,v); 5m apart starting at start_ms."""
    return [mk(start_ms + i * 300_000, *s) for i, s in enumerate(specs)]


def make_history(start_ms, n=40, price=100.0, vol=1000.0):
    """Flat warm-up candles before the session (for ATR/vol averages)."""
    out = []
    for i in range(n):
        ts = start_ms - (n - i) * 300_000
        out.append(mk(ts, price, price + 1.0, price - 1.0, price, vol))
    return out


def utc(y, m, d, hh, mm):
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp() * 1000)


print("indicators:")
check("ema computes", abs(ema([1] * 30, 9)[-1] - 1.0) < 1e-9)
hist = make_history(utc(2026, 6, 1, 0, 0))
check("atr ~2.0 on flat 2-range candles", abs(atr(hist, 14) - 2.0) < 1e-6)
check("avg_volume", abs(avg_volume(hist + [mk(0, 0, 0, 0, 0, 9999)], 20) - 1000) < 1e-6)
vw = session_vwap([mk(0, 100, 102, 98, 100, 10), mk(1, 100, 104, 100, 104, 10)], 0)
check("vwap weighted mid", abs(vw - ((100 + 102.6666666) / 2)) < 0.01)

print("session detection:")
now = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)
sess, start = strategy.current_session(now)
check("14:00 UTC inside US_OPEN", sess and sess["name"] == "US_OPEN")
check("session start 13:30", start.hour == 13 and start.minute == 30)
sess2, _ = strategy.current_session(datetime(2026, 6, 1, 11, 0, tzinfo=timezone.utc))
check("11:00 UTC outside both sessions", sess2 is None)
sess3, _ = strategy.current_session(datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc))
check("03:00 UTC inside UTC_OPEN", sess3 and sess3["name"] == "UTC_OPEN")

print("opening range:")
s0 = utc(2026, 6, 1, 13, 30)
sess_us = {"name": "US_OPEN", "hour": 13, "minute": 30}
start_dt = datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc)
range_candles = candles_for_session(s0, [
    (100, 101.0, 99.5, 100.5, 1000),
    (100.5, 101.5, 100.0, 101.0, 1000),
    (101, 101.2, 99.8, 100.0, 1000),
    (100, 100.8, 99.6, 100.2, 1000),
    (100.2, 101.4, 100.0, 101.2, 1000),
    (101.2, 101.6, 100.4, 100.8, 1000),
])  # range: high 101.6, low 99.5
full = make_history(s0) + range_candles
orange = strategy.build_opening_range(full, sess_us, start_dt)
check("range built after 6 candles", orange is not None)
check("range high", abs(orange.high - 101.6) < 1e-9)
check("range low", abs(orange.low - 99.5) < 1e-9)
check("incomplete range returns None",
      strategy.build_opening_range(full[:-1], sess_us, start_dt) is None)
ok, why = strategy.range_is_tradeable(orange, full)
check(f"range filter ({why})", ok)

print("breakout signals:")
# Long breakout: close above 101.6, big volume, above VWAP
bo = mk(s0 + 6 * 300_000, 101.0, 102.5, 100.9, 102.2, 2500)
sig = strategy.check_breakout(full + [bo], orange)
check("long signal fires", sig is not None and sig.side == "Buy")
check("long stop at range low", sig and abs(sig.stop - 99.5) < 1e-9)
check("long TP at config R-multiple",
      sig and abs(sig.take_profit - (102.2 + config.TP_R_MULT * (102.2 - 99.5))) < 1e-9)

# Same breakout but weak volume -> no signal
weak = mk(s0 + 6 * 300_000, 101.0, 102.5, 100.9, 102.2, 1100)
check("weak volume rejected", strategy.check_breakout(full + [weak], orange) is None)

# Close inside range -> no signal
inside = mk(s0 + 6 * 300_000, 101.0, 101.5, 100.5, 101.1, 2500)
check("inside-range close rejected", strategy.check_breakout(full + [inside], orange) is None)

# Short breakout: close below 99.5 with volume (price below VWAP by construction)
sh = mk(s0 + 6 * 300_000, 100.0, 100.1, 98.0, 98.4, 2600)
sig2 = strategy.check_breakout(full + [sh], orange)
check("short signal fires", sig2 is not None and sig2.side == "Sell")
check("short stop at range high", sig2 and abs(sig2.stop - 101.6) < 1e-9)

# Candle during the opening range itself -> never a signal
during = mk(s0 + 3 * 300_000, 100, 105, 99, 104.9, 9000)
check("no signal during range window",
      strategy.check_breakout(make_history(s0) + range_candles[:3] + [during], orange) is None)

# After session close -> no signal
late = mk(orange.close_ms + 300_000, 101.0, 102.5, 100.9, 102.2, 2500)
check("no signal after session close", strategy.check_breakout(full + [late], orange) is None)

print("breakeven logic:")
check("BE not yet at +0.5R", not strategy.should_move_to_breakeven("Buy", 100, 98, 101.0))
check("BE at +1R long", strategy.should_move_to_breakeven("Buy", 100, 98, 102.0))
check("BE at +1R short", strategy.should_move_to_breakeven("Sell", 100, 102, 98.0))

print("risk sizing:")
import risk
check("1% of 10k, $2 stop -> 50", abs(risk.position_size(10_000, 100, 98, 0.1, 0.1) - 50.0) < 1e-9)
check("below min qty -> 0", risk.position_size(100, 100, 98, 0.1, 1.0) == 0.0)
check("rounds to step", risk.position_size(10_000, 100, 97, 1.0, 1.0) == 33.0)

print(f"\n{PASS} passed, {FAIL} failed")
exit(1 if FAIL else 0)
