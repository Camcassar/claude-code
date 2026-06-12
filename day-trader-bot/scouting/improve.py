"""
Velocity-Z v2 — improved version of the Hilbert F36 theory, tested honestly.

Baseline (their theory, our signal): EMA(8) 3-bar velocity, z-score(144),
enter |z|>=2.5 next-bar open, TP 5.5% / SL 2.5%, one position at a time.

Improvement candidates (each tested in/out of sample, fees included):
  A  ATR-scaled brackets  : TP = 5.2*ATR24, SL = 2.4*ATR24 (same ~2.2 ratio)
  B  mid-vol filter       : only trade when 0.45% <= ATR24% <= 1.35%
  C  trend alignment      : only trade with sign of 400h return
  D  fixed-risk sizing    : risk 2% of equity per trade (vs all-in notional)
Train: 2024-01 .. 2025-12   Test (out-of-sample): 2026-01 .. now
"""

import csv
import math
from datetime import datetime, timezone

from fit_signal import load_candles, ema, zscores

FEE = 0.00055          # taker, per side
EMA_N, LAG, ZWIN, THR = 8, 3, 144, 2.5


def prep(candles):
    closes = [c["close"] for c in candles]
    base = ema(closes, EMA_N)
    vel = [0.0] * LAG + [(base[i] - base[i - LAG]) / base[i - LAG]
                         for i in range(LAG, len(base))]
    z = zscores(vel, ZWIN)
    # ATR24 %
    atrp = [None] * len(candles)
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        if len(trs) >= 24:
            atrp[i] = sum(trs[-24:]) / 24 / candles[i]["close"]
    # 400h trend
    trend = [None] * len(candles)
    for i in range(400, len(candles)):
        trend[i] = candles[i]["close"] - candles[i - 400]["close"]
    return z, atrp, trend


def run(candles, z, atrp, trend, lo, hi,
        atr_brackets=False, vol_filter=False, trend_filter=False,
        risk_sizing=False, start_eq=10_000.0):
    eq = start_eq
    peak, maxdd = eq, 0.0
    pos = None
    trades = []
    for i in range(lo, hi):
        c = candles[i]
        if pos:
            exit_px = None
            if pos["side"] == 1:
                if c["low"] <= pos["sl"]:
                    exit_px = pos["sl"]
                elif c["high"] >= pos["tp"]:
                    exit_px = pos["tp"]
            else:
                if c["high"] >= pos["sl"]:
                    exit_px = pos["sl"]
                elif c["low"] <= pos["tp"]:
                    exit_px = pos["tp"]
            if exit_px is not None:
                gross = pos["side"] * (exit_px - pos["px"]) * pos["qty"]
                fees = FEE * pos["qty"] * (pos["px"] + exit_px)
                pnl = gross - fees
                eq += pnl
                trades.append(pnl)
                peak = max(peak, eq)
                maxdd = max(maxdd, (peak - eq) / peak)
                pos = None
        if pos is None and z[i] is not None and i + 1 < hi:
            side = 1 if z[i] >= THR else (-1 if z[i] <= -THR else 0)
            if side == 0:
                continue
            if vol_filter and (atrp[i] is None
                               or not 0.0045 <= atrp[i] <= 0.0135):
                continue
            if trend_filter and (trend[i] is None
                                 or (side == 1) != (trend[i] > 0)):
                continue
            px = candles[i + 1]["open"]
            if atr_brackets and atrp[i]:
                tp_d, sl_d = 5.2 * atrp[i], 2.4 * atrp[i]
            else:
                tp_d, sl_d = 0.055, 0.025
            if risk_sizing:
                qty = (0.02 * eq) / (sl_d * px)   # risk 2% of equity
            else:
                qty = eq / px                      # 1x notional all-in
            pos = {"side": side, "px": px, "qty": qty,
                   "tp": px * (1 + side * tp_d), "sl": px * (1 - side * sl_d)}
    wins = [t for t in trades if t > 0]
    gl = -sum(t for t in trades if t <= 0)
    pf = sum(wins) / gl if gl > 0 else float("inf")
    return {"net%": (eq / start_eq - 1) * 100, "maxDD%": maxdd * 100,
            "trades": len(trades), "win%": len(wins) / max(len(trades), 1) * 100,
            "PF": pf}


if __name__ == "__main__":
    candles = load_candles()
    z, atrp, trend = prep(candles)
    ts = [c["ts"] for c in candles]

    def at(y, m, d):
        t = int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)
        return next(i for i, v in enumerate(ts) if v >= t)

    train = (at(2024, 1, 1), at(2026, 1, 1))
    test = (at(2026, 1, 1), len(candles))

    variants = [
        ("baseline (their theory)", {}),
        ("A: ATR brackets", {"atr_brackets": True}),
        ("B: mid-vol filter", {"vol_filter": True}),
        ("C: trend filter", {"trend_filter": True}),
        ("A+B", {"atr_brackets": True, "vol_filter": True}),
        ("A+C", {"atr_brackets": True, "trend_filter": True}),
        ("A+B+C", {"atr_brackets": True, "vol_filter": True,
                   "trend_filter": True}),
    ]
    print(f"{'variant':26s} | {'TRAIN 24-25':>34s} | {'TEST 2026 (OOS)':>34s}")
    for name, kw in variants:
        r1 = run(candles, z, atrp, trend, *train, **kw)
        r2 = run(candles, z, atrp, trend, *test, **kw)
        f = lambda r: (f"net {r['net%']:+7.1f}% DD {r['maxDD%']:4.1f}% "
                       f"PF {r['PF']:4.2f} n={r['trades']:3d}")
        print(f"{name:26s} | {f(r1)} | {f(r2)}")
    print()
    print("with 2%-risk sizing (D) on the best logic variants:")
    for name, kw in variants:
        kw = dict(kw, risk_sizing=True)
        r1 = run(candles, z, atrp, trend, *train, **kw)
        r2 = run(candles, z, atrp, trend, *test, **kw)
        f = lambda r: (f"net {r['net%']:+7.1f}% DD {r['maxDD%']:4.1f}% "
                       f"PF {r['PF']:4.2f} n={r['trades']:3d}")
        print(f"{name+' +D':26s} | {f(r1)} | {f(r2)}")
