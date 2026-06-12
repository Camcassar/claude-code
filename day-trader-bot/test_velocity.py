"""
Unit tests — Velocity-Z v3 signal, sizing, state and price rounding.
Fully offline (synthetic candles, no API calls). Run: python test_velocity.py
"""

import os
import tempfile
from decimal import Decimal

import velocity_bot as vb

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


def mk_series(n, drift, spike=0.0, spike_bars=3):
    """Synthetic 1h candles: geometric drift per bar, then an end spike
    spread over the last `spike_bars` bars."""
    out, px = [], 1000.0
    for i in range(n):
        px *= (1 + drift)
        if i >= n - spike_bars:
            px *= (1 + spike)
        out.append({"ts": i * 3_600_000, "open": px, "high": px * 1.001,
                    "low": px * 0.999, "close": px, "volume": 100.0})
    return out


print("signal:")
check("not enough candles -> 0", vb.signal(mk_series(100, 0.0)) == 0)

up = mk_series(600, 0.0002, spike=0.01)        # uptrend + upward burst
check("up-spike with uptrend -> LONG", vb.signal(up) == 1)

down = mk_series(600, -0.0002, spike=-0.01)    # downtrend + downward burst
check("down-spike with downtrend -> SHORT", vb.signal(down) == -1)

ct = mk_series(600, -0.0006, spike=0.012)      # up-spike against downtrend
check("up-spike against downtrend blocked", vb.signal(ct) == 0)

flat = mk_series(600, 0.0002)                  # no burst
check("no spike -> 0", vb.signal(flat) == 0)

print("sizing:")
q = vb.position_qty(10_000, 100, 0.01)
check("2% risk, slip-buffered stop", abs(q - 66.66) < 1e-9)
check("qty floors to step", vb.position_qty(10_000, 100, 1.0) == 66.0)

print("state:")
tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
tmp.close()
os.unlink(tmp.name)
st = vb.BotState(path=tmp.name)
check("fresh state: no cooldown", not st.cooldown_active("Buy", 10**13))
check("fresh state: streak 0", st.loss_streak == 0)

now = 10**12
tripped = False
for i in range(vb.CB_LOSSES):
    tripped = st.book_result({"ts": now + i, "pnl": -50.0, "side": "Buy"},
                             now_bar_ms=now)
check("breaker trips on 4th straight loss", tripped)
check("pause set ~CB_PAUSE_BARS ahead",
      st.pause_until_ms == now + vb.CB_PAUSE_BARS * vb.BAR_MS)
check("Buy cooldown active after Buy loss",
      st.cooldown_active("Buy", now + 3 * 3_600_000))
check("Sell side not in cooldown", not st.cooldown_active("Sell", now))
check("cooldown expires after COOLDOWN_H",
      not st.cooldown_active("Buy", now + (vb.COOLDOWN_H + 1) * 3_600_000))
st.book_result({"ts": now + 100, "pnl": +80.0, "side": "Buy"}, now_bar_ms=now)
check("win clears streak and side cooldown",
      st.loss_streak == 0 and not st.cooldown_active("Buy", now + 200))

st.update_high_water(10_000)
check("no floor at high-water", not st.below_floor(10_000))
check("floor trips below 75% of HWM", st.below_floor(7_400))

st2 = vb.BotState(path=tmp.name)               # reload from disk
check("state survives restart",
      st2.high_water == 10_000 and st2.pause_until_ms == st.pause_until_ms)
os.unlink(tmp.name)

print("price rounding:")
from exchange import Bybit
ex = Bybit.__new__(Bybit)                      # no HTTP needed
ex._tick = Decimal("0.01")
check("rounds to 0.01 tick", ex.round_price(2345.6789) == "2345.68")
ex._tick = Decimal("0.5")
check("rounds to 0.5 tick", ex.round_price(2345.6) == "2345.5")

print("backtest engine:")
import backtest_velocity as bt
z, trend = bt.precompute(up)
check("precompute aligns arrays", len(z) == len(trend) == len(up))
r = bt.run(up, z, trend, 0, len(up), trend_filter=True, risk_sizing=True)
check("engine runs on synthetic data", isinstance(r["trades"], int))

print(f"\n{PASS} passed, {FAIL} failed")
exit(1 if FAIL else 0)
