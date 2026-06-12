"""
Forensic verification of the 'Hilbert F36 ETH 1h' trader.dev backtest
against real Bybit ETHUSDT 1h data.

Checks per trade:
  A. entry price matches a real candle at the entry timestamp (open/close)
  B. claimed TP/SL exit level was actually reachable on the exit candle
  C. no earlier bar between entry and exit hit the OPPOSITE bracket first
     (i.e. the backtest didn't take an impossible/optimistic fill)
Recomputes: equity curve, true max drawdown, win rate, PF.
"""

import csv
import sys
import time
from datetime import datetime, timezone

from pybit.unified_trading import HTTP

SYMBOL = "ETHUSDT"
TP = 0.055
SL = 0.025
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 6, 9, tzinfo=timezone.utc)
TOL = 0.005  # 0.5% price tolerance vs Bybit (their feed may differ slightly)

MONTHS = {m: i + 1 for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}


def parse_trades(path):
    """Assign years by monotonic ordering starting 2024."""
    trades = []
    year = 2024
    prev = None
    with open(path) as f:
        for row in csv.DictReader(f):
            def dt(s, base_year):
                mon, day, hm = s.split()[0], int(s.split()[1]), s.split()[2]
                h, m = map(int, hm.split(":"))
                return datetime(base_year, MONTHS[mon], day, h, m,
                                tzinfo=timezone.utc)
            e = dt(row["entry_dt"], year)
            if prev and e < prev:
                year += 1
                e = dt(row["entry_dt"], year)
            x = dt(row["exit_dt"], year)
            if x < e:
                x = dt(row["exit_dt"], year + 1)
            prev = e
            trades.append({
                "n": int(row["n"]), "side": row["side"],
                "entry_dt": e, "entry_px": float(row["entry_px"]),
                "exit_dt": x, "exit_px": float(row["exit_px"]),
                "qty": float(row["qty"]), "net_pnl": float(row["net_pnl"]),
                "pnl_pct": float(row["pnl_pct"]),
            })
    return trades


def fetch_candles():
    http = HTTP(testnet=False)
    out = {}
    cur = int(START.timestamp() * 1000)
    end = int(END.timestamp() * 1000)
    step = 1000 * 3_600_000
    while cur < end:
        for attempt in range(3):
            try:
                r = http.get_kline(category="linear", symbol=SYMBOL,
                                   interval="60", start=cur,
                                   end=min(cur + step, end), limit=1000)
                break
            except Exception as e:
                if attempt == 2:
                    raise
                time.sleep(2)
        for row in r["result"]["list"]:
            out[int(row[0])] = {
                "open": float(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]),
            }
        cur += step
    return out


def hour_ms(dt):
    return int(dt.timestamp() * 1000)


def verify(trades, candles, shift_ms=0):
    entry_match = entry_miss = 0
    exit_ok = exit_bad = 0
    path_ok = path_bad = 0
    issues = []

    for t in trades:
        ekey = hour_ms(t["entry_dt"]) + shift_ms
        c = candles.get(ekey)
        # A: entry price plausible on entry bar (within bar range, near open)
        if c and (c["low"] * (1 - TOL) <= t["entry_px"] <= c["high"] * (1 + TOL)):
            entry_match += 1
        else:
            entry_miss += 1
            if c:
                issues.append(f"#{t['n']} entry {t['entry_px']} outside bar "
                              f"[{c['low']}, {c['high']}] @ {t['entry_dt']}")
            else:
                issues.append(f"#{t['n']} no candle at {t['entry_dt']}")
            continue

        # exit levels
        if t["side"] == "LONG":
            tp_px = t["entry_px"] * (1 + TP)
            sl_px = t["entry_px"] * (1 - SL)
        else:
            tp_px = t["entry_px"] * (1 - TP)
            sl_px = t["entry_px"] * (1 + SL)
        won = t["pnl_pct"] > 0
        target = tp_px if won else sl_px

        # B: exit bar must reach the claimed level
        xkey = hour_ms(t["exit_dt"]) + shift_ms
        xc = candles.get(xkey) or candles.get(xkey - 3_600_000)
        if xc and xc["low"] * (1 - TOL) <= target <= xc["high"] * (1 + TOL):
            exit_ok += 1
        else:
            exit_bad += 1
            if xc:
                issues.append(f"#{t['n']} exit level {target:.2f} not in exit bar "
                              f"[{xc['low']}, {xc['high']}] @ {t['exit_dt']}")
            continue

        # C: walk the path — opposite level must not be hit first
        bad = False
        k = ekey + 3_600_000  # first full bar after entry
        while k < xkey:
            pc = candles.get(k)
            if pc:
                if won:
                    # winner: SL must never have been touched first
                    if t["side"] == "LONG" and pc["low"] <= sl_px * (1 - TOL):
                        bad = True
                    if t["side"] == "SHORT" and pc["high"] >= sl_px * (1 + TOL):
                        bad = True
                else:
                    # loser: TP must never have been touched first
                    if t["side"] == "LONG" and pc["high"] >= tp_px * (1 + TOL):
                        bad = True
                    if t["side"] == "SHORT" and pc["low"] <= tp_px * (1 - TOL):
                        bad = True
            if bad:
                issues.append(f"#{t['n']} opposite bracket hit before claimed "
                              f"exit ({t['side']}, won={won})")
                break
            k += 3_600_000
        if bad:
            path_bad += 1
        else:
            path_ok += 1

    return entry_match, entry_miss, exit_ok, exit_bad, path_ok, path_bad, issues


def recompute_equity(trades, start=10_000.0):
    eq = start
    peak = start
    max_dd = 0.0
    for t in trades:
        eq += t["net_pnl"]
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak)
    return eq, max_dd


if __name__ == "__main__":
    trades = parse_trades(sys.argv[1] if len(sys.argv) > 1
                          else "hilbert_f36_trades.csv")
    print(f"{len(trades)} trades parsed "
          f"({trades[0]['entry_dt']:%Y-%m-%d} → {trades[-1]['exit_dt']:%Y-%m-%d})")

    candles = fetch_candles()
    print(f"{len(candles)} Bybit 1h candles fetched")

    best = None
    for shift_h in range(-14, 15):
        em, emi, eo, eb, po, pb, iss = verify(trades, candles,
                                              shift_h * 3_600_000)
        score = em + eo + po
        if best is None or score > best[0]:
            best = (score, shift_h, em, emi, eo, eb, po, pb, iss)
    _, shift_h, em, emi, eo, eb, po, pb, iss = best

    print(f"\nBest timestamp alignment: shift {shift_h:+d}h")
    print(f"A. entry price matches real bar : {em}/{em+emi}")
    print(f"B. exit level reachable on bar  : {eo}/{eo+eb}")
    print(f"C. no opposite-bracket-first    : {po}/{po+pb}")

    eq, dd = recompute_equity(trades)
    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    gp = sum(t["net_pnl"] for t in trades if t["net_pnl"] > 0)
    gl = -sum(t["net_pnl"] for t in trades if t["net_pnl"] < 0)
    print(f"\nRecomputed from trade list: end equity ${eq:,.0f} "
          f"({(eq/10000-1)*100:+.1f}%), TRUE max DD {dd*100:.1f}%, "
          f"win rate {wins/len(trades)*100:.1f}%, PF {gp/gl:.2f}")
    print(f"(report claimed +664.6%, max DD 7.9%)")

    if iss:
        print(f"\n{len(iss)} issues:")
        for s in iss[:25]:
            print("  " + s)
