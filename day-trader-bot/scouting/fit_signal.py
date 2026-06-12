"""
Reverse-engineer the Hilbert F36 entry signal from its 136 verified trades.

Hypothesis from the strategy name ("Hilbert F36 — minVelZ=2.0 TP=5.5 SL=2.5"):
entry when the z-score of smoothed price VELOCITY exceeds 2.0 (long) or
-2.0 (short), with fixed TP/SL brackets. We grid-search smoothing/lag/window
variants, simulate with the same brackets + only-one-position rule, and score
how well each variant reproduces their actual trade entries.
"""

import csv
import math
import sys
from datetime import datetime, timezone, timedelta

SHIFT_H = -10  # report displays Brisbane time; convert to UTC
TP, SL = 0.055, 0.025

MONTHS = {m: i + 1 for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}


def load_candles(path="eth_1h.csv"):
    out = []
    with open(path) as f:
        for r in csv.DictReader(f):
            out.append({"ts": int(r["ts"]), "open": float(r["open"]),
                        "high": float(r["high"]), "low": float(r["low"]),
                        "close": float(r["close"]), "volume": float(r["volume"])})
    return out


def load_trades(path="hilbert_f36_trades.csv"):
    trades, year, prev = [], 2024, None
    with open(path) as f:
        for row in csv.DictReader(f):
            def dt(s, y):
                p = s.split()
                h, mi = map(int, p[2].split(":"))
                return datetime(y, MONTHS[p[0]], int(p[1]), h, mi,
                                tzinfo=timezone.utc)
            e = dt(row["entry_dt"], year)
            if prev and e < prev:
                year += 1
                e = dt(row["entry_dt"], year)
            prev = e
            e_utc = e + timedelta(hours=SHIFT_H)
            trades.append({"n": int(row["n"]), "side": row["side"],
                           "ts": int(e_utc.timestamp() * 1000),
                           "entry_px": float(row["entry_px"]),
                           "pnl_pct": float(row["pnl_pct"])})
    return trades


def ema(vals, n):
    k = 2 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def zscores(vals, w):
    out = [None] * len(vals)
    s = s2 = 0.0
    from collections import deque
    q = deque()
    for i, v in enumerate(vals):
        q.append(v); s += v; s2 += v * v
        if len(q) > w:
            old = q.popleft(); s -= old; s2 -= old * old
        if len(q) == w:
            m = s / w
            var = max(s2 / w - m * m, 1e-18)
            out[i] = (v - m) / math.sqrt(var)
    return out


def simulate(candles, z, thr):
    """Bracket simulation: enter next bar open when |z|>=thr and flat."""
    trades = []
    pos = None
    for i in range(1, len(candles) - 1):
        c = candles[i]
        if pos:
            if pos["side"] == "LONG":
                if c["low"] <= pos["sl"]:
                    trades.append((pos["i"], pos["side"], -1)); pos = None
                elif c["high"] >= pos["tp"]:
                    trades.append((pos["i"], pos["side"], 1)); pos = None
            else:
                if c["high"] >= pos["sl"]:
                    trades.append((pos["i"], pos["side"], -1)); pos = None
                elif c["low"] <= pos["tp"]:
                    trades.append((pos["i"], pos["side"], 1)); pos = None
        if pos is None and z[i] is not None:
            if z[i] >= thr:
                px = candles[i + 1]["open"]
                pos = {"i": i + 1, "side": "LONG",
                       "tp": px * (1 + TP), "sl": px * (1 - SL)}
            elif z[i] <= -thr:
                px = candles[i + 1]["open"]
                pos = {"i": i + 1, "side": "SHORT",
                       "tp": px * (1 - TP), "sl": px * (1 + SL)}
    return trades


def score(sim_trades, real, idx_of_ts, tol=2):
    """recall: their entries matched by ours (same side, +-tol bars);
    precision: our entries that match theirs."""
    sim_set = [(i, s) for i, s, _ in sim_trades]
    matched = 0
    for t in real:
        i = idx_of_ts.get(t["ts"])
        if i is None:
            continue
        if any(abs(si - i) <= tol and ss == t["side"] for si, ss in sim_set):
            matched += 1
    prec = 0
    for si, ss in sim_set:
        if any(abs(idx_of_ts.get(t["ts"], -99) - si) <= tol
               and t["side"] == ss for t in real):
            prec += 1
    return matched, len(real), prec, len(sim_set)


if __name__ == "__main__":
    candles = load_candles()
    real = load_trades()
    idx_of_ts = {c["ts"]: i for i, c in enumerate(candles)}
    # restrict sim to their window
    t0 = real[0]["ts"] - 200 * 3_600_000
    t1 = int(datetime(2026, 6, 8, tzinfo=timezone.utc).timestamp() * 1000)
    lo = next(i for i, c in enumerate(candles) if c["ts"] >= t0)
    hi = next(i for i, c in enumerate(candles) if c["ts"] >= t1)
    window = candles[lo:hi]
    widx = {c["ts"]: i for i, c in enumerate(window)}

    closes = [c["close"] for c in window]
    results = []
    for sm in (1, 3, 5, 8, 10, 14, 21, 36):
        base = ema(closes, sm) if sm > 1 else closes
        for lag in (1, 2, 3, 4, 6):
            vel = [0.0] * lag + [(base[i] - base[i - lag]) / base[i - lag]
                                 for i in range(lag, len(base))]
            for w in (24, 36, 48, 72, 96, 144):
                z = zscores(vel, w)
                for thr in (1.5, 1.75, 2.0, 2.25, 2.5):
                    sim = simulate(window, z, thr)
                    if not (60 <= len(sim) <= 260):
                        continue
                    m, nr, p, ns = score(sim, real, widx)
                    results.append((m / nr, p / max(ns, 1), sm, lag, w, thr,
                                    len(sim)))
    results.sort(reverse=True)
    print("recall | prec | ema | lag | zwin | thr | n_sim")
    for r in results[:12]:
        print(f"{r[0]*100:5.1f}% | {r[1]*100:5.1f}% | {r[2]:3d} | {r[3]} | "
              f"{r[4]:3d} | {r[5]} | {r[6]}")
