"""
Backtest — Velocity-Z v3 strategy over historical ETHUSDT 1h candles.

Usage:
    python backtest_velocity.py               # fetch from Bybit public API
    python backtest_velocity.py --csv eth.csv # replay offline
    python backtest_velocity.py --save eth.csv --days 900   # fetch + save

Replays the exact velocity_bot.py logic (signal on closed bar, fill at the
NEXT bar open, conservative intrabar ordering: SL checked before TP, taker
fees both sides) and prints a train (2024-25) / out-of-sample (2026+) table
with one-at-a-time ablations of every v3 rule, so nothing gets adopted
without holding up out-of-sample. Run this BEFORE changing any parameter
in velocity_bot.py.
"""

import argparse
import csv
import sys
from datetime import datetime, timezone
from math import sqrt

import velocity_bot as vb

FEE = 0.00055   # taker, per side
START_EQUITY = 10_000.0


# ── data ─────────────────────────────────────────────────────────────
def fetch_history(days):
    from pybit.unified_trading import HTTP
    http = HTTP(testnet=False)
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = end - days * 86_400_000
    out = {}
    cursor = start
    step = 1000 * 3_600_000
    while cursor < end:
        r = http.get_kline(category="linear", symbol=vb.SYMBOL, interval="60",
                           start=cursor, end=min(cursor + step, end), limit=1000)
        for row in r["result"]["list"]:
            out[int(row[0])] = {
                "ts": int(row[0]), "open": float(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]),
                "volume": float(row[5]),
            }
        cursor += step
    return [out[k] for k in sorted(out)]


def load_csv(path):
    out = []
    with open(path) as f:
        for row in csv.DictReader(f):
            out.append({k: (int(row[k]) if k == "ts" else float(row[k]))
                        for k in ("ts", "open", "high", "low", "close",
                                  "volume")})
    return sorted(out, key=lambda c: c["ts"])


def save_csv(candles, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ts", "open", "high", "low",
                                          "close", "volume"])
        w.writeheader()
        w.writerows(candles)


# ── signal precompute (same math as velocity_bot.signal) ─────────────
def precompute(candles):
    closes = [c["close"] for c in candles]
    k = 2 / (vb.EMA_N + 1)
    base = [closes[0]]
    for v in closes[1:]:
        base.append(v * k + base[-1] * (1 - k))
    vel = [0.0] * vb.VEL_LAG + [
        (base[i] - base[i - vb.VEL_LAG]) / base[i - vb.VEL_LAG]
        for i in range(vb.VEL_LAG, len(base))]

    z = [None] * len(vel)
    s = s2 = 0.0
    from collections import deque
    q = deque()
    for i, v in enumerate(vel):
        q.append(v); s += v; s2 += v * v
        if len(q) > vb.Z_WIN:
            old = q.popleft(); s -= old; s2 -= old * old
        if len(q) == vb.Z_WIN:
            m = s / vb.Z_WIN
            var = max(s2 / vb.Z_WIN - m * m, 1e-18)
            z[i] = (vel[i] - m) / sqrt(var)

    trend = [None] * len(candles)
    for i in range(vb.TREND_BARS, len(candles)):
        trend[i] = closes[i] - closes[i - vb.TREND_BARS]
    return z, trend


# ── engine ───────────────────────────────────────────────────────────
def run(candles, z, trend, lo, hi, *, trend_filter=True, cooldown_h=0,
        breaker=None, flip=False, risk_sizing=True, slip_buffer=1.0,
        start_eq=START_EQUITY):
    """breaker: (n_losses, pause_bars) or None."""
    eq, peak, maxdd = start_eq, start_eq, 0.0
    pos = None
    trades = []
    loss_streak = 0
    pause_until = -1          # bar index
    last_loss_exit = {}       # side(+1/-1) -> bar index of loss exit

    def book_exit(i, exit_px, why):
        nonlocal eq, peak, maxdd, pos, loss_streak, pause_until
        gross = pos["side"] * (exit_px - pos["px"]) * pos["qty"]
        fees = FEE * pos["qty"] * (pos["px"] + exit_px)
        pnl = gross - fees
        eq += pnl
        trades.append({"pnl": pnl, "side": pos["side"], "why": why,
                       "ts": candles[i]["ts"]})
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak)
        if pnl < 0:
            loss_streak += 1
            last_loss_exit[pos["side"]] = i
            if breaker and loss_streak >= breaker[0]:
                pause_until = i + breaker[1]
                loss_streak = 0
        else:
            loss_streak = 0
            last_loss_exit.pop(pos["side"], None)
        pos = None

    def gated(side, i):
        """True if entry on bar i+1 from signal bar i is blocked."""
        if i < pause_until:
            return True
        if trend_filter and (trend[i] is None or (side == 1) != (trend[i] > 0)):
            return True
        ll = last_loss_exit.get(side)
        if cooldown_h and ll is not None and (i - ll) < cooldown_h:
            return True
        return False

    for i in range(lo, hi):
        c = candles[i]
        # exits first (conservative: SL before TP)
        if pos:
            if pos["side"] == 1:
                if c["low"] <= pos["sl"]:
                    book_exit(i, pos["sl"], "sl")
                elif c["high"] >= pos["tp"]:
                    book_exit(i, pos["tp"], "tp")
            else:
                if c["high"] >= pos["sl"]:
                    book_exit(i, pos["sl"], "sl")
                elif c["low"] <= pos["tp"]:
                    book_exit(i, pos["tp"], "tp")

        if z[i] is None or i + 1 >= hi:
            continue
        side = 1 if z[i] >= vb.Z_THR else (-1 if z[i] <= -vb.Z_THR else 0)
        if side == 0:
            continue

        if pos and flip and side != pos["side"] and not gated(side, i):
            book_exit(i + 1, candles[i + 1]["open"], "flip")
        if pos or gated(side, i):
            continue

        px = candles[i + 1]["open"]
        if risk_sizing:
            qty = (vb.RISK_PCT / 100 * eq) / (vb.SL_PCT * slip_buffer * px)
        else:
            qty = eq / px
        pos = {"side": side, "px": px, "qty": qty,
               "tp": px * (1 + side * vb.TP_PCT),
               "sl": px * (1 - side * vb.SL_PCT)}

    wins = [t for t in trades if t["pnl"] > 0]
    gl = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    pf = sum(t["pnl"] for t in wins) / gl if gl > 0 else float("inf")
    return {"net%": (eq / start_eq - 1) * 100, "maxDD%": maxdd * 100,
            "trades": len(trades),
            "win%": len(wins) / max(len(trades), 1) * 100, "PF": pf}


def fmt(r):
    return (f"net {r['net%']:+7.1f}% DD {r['maxDD%']:4.1f}% "
            f"PF {r['PF']:4.2f} n={r['trades']:3d}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=900)
    ap.add_argument("--csv", help="replay from CSV instead of fetching")
    ap.add_argument("--save", help="save fetched candles to CSV")
    args = ap.parse_args()

    candles = load_csv(args.csv) if args.csv else fetch_history(args.days)
    if not candles:
        sys.exit("No candle data.")
    if args.save:
        save_csv(candles, args.save)
        print(f"saved {len(candles)} candles to {args.save}")

    z, trend = precompute(candles)
    ts = [c["ts"] for c in candles]

    def at(y, m, d):
        t = int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)
        return next((i for i, v in enumerate(ts) if v >= t), len(ts))

    train = (at(2024, 1, 1), at(2026, 1, 1))
    test = (at(2026, 1, 1), len(candles))

    V3 = dict(trend_filter=True, cooldown_h=vb.COOLDOWN_H,
              breaker=(vb.CB_LOSSES, vb.CB_PAUSE_BARS), flip=vb.ALLOW_FLIP,
              risk_sizing=True, slip_buffer=vb.SLIP_BUFFER)
    variants = [
        ("v2 spec (trend+risk)", dict(trend_filter=True, risk_sizing=True)),
        ("v3 full", V3),
        ("v3 minus cooldown", dict(V3, cooldown_h=0)),
        ("v3 minus breaker", dict(V3, breaker=None)),
        ("v3 minus flip", dict(V3, flip=False)),
        ("v3 minus slip buffer", dict(V3, slip_buffer=1.0)),
        ("v3 minus trend filter", dict(V3, trend_filter=False)),
    ]
    print(f"{len(candles)} candles "
          f"({datetime.fromtimestamp(ts[0]/1000, tz=timezone.utc):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(ts[-1]/1000, tz=timezone.utc):%Y-%m-%d})\n")
    print(f"{'variant':24s} | {'TRAIN 2024-25':>36s} | {'TEST 2026+ (OOS)':>36s}")
    for name, kw in variants:
        r1 = run(candles, z, trend, *train, **kw)
        r2 = run(candles, z, trend, *test, **kw)
        print(f"{name:24s} | {fmt(r1)} | {fmt(r2)}")
    print("\nAdopt a rule only if it does not degrade the OOS column.")
