"""
Loss-pattern analysis of the Hilbert F36 trade list (136 verified trades).

Runs entirely offline from hilbert_f36_trades.csv — no exchange access
needed. Goal: characterise WHERE the losses cluster so v3 changes target
real failure modes instead of curve-fitting noise.

Patterns examined:
  1. baseline stats by side / year (sanity vs REPORT.md)
  2. exit-slippage anomalies (losses worse than the -2.58% bracket implies)
  3. hold-time distribution of winners vs losers
  4. re-entry gap: outcome vs hours since the previous trade's exit,
     split by previous outcome ("revenge chain" hypothesis)
  5. same-side loss chains (lengths, total damage)
  6. simulated risk rules ON THE TRADE LIST (approximation — removing a
     trade can only unlock entries we can't observe, so trade counts are
     a lower bound):
       R1 circuit breaker (v2 spec: 4 straight losses -> 72h pause)
       R2 same-side cooldown: skip same-side entry within N hours of a
          same-side LOSS exit
"""

import csv
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta

MONTHS = {m: i + 1 for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
HERE = os.path.dirname(os.path.abspath(__file__))


def load(path=None):
    """Same monotonic year-assignment as verify_hilbert.py."""
    trades, year, prev = [], 2024, None
    with open(path or os.path.join(HERE, "hilbert_f36_trades.csv")) as f:
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
            x = dt(row["exit_dt"], year)
            if x < e:
                x = dt(row["exit_dt"], year + 1)
            prev = e
            trades.append({
                "n": int(row["n"]), "side": row["side"],
                "entry": e, "exit": x, "bars": int(row["bars"]),
                "pnl": float(row["net_pnl"]), "pct": float(row["pnl_pct"]),
            })
    return trades


def stats(ts):
    if not ts:
        return "n=0"
    wins = [t for t in ts if t["pnl"] > 0]
    gw = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in ts if t["pnl"] <= 0)
    pf = gw / gl if gl else float("inf")
    return (f"n={len(ts):3d} win%={len(wins)/len(ts)*100:4.1f} "
            f"net=${sum(t['pnl'] for t in ts):+9.0f} PF={pf:4.2f}")


def equity_dd(ts, start=10_000.0):
    eq, peak, dd = start, start, 0.0
    for t in ts:
        eq += t["pnl"]
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    return eq, dd


def main():
    tr = load()
    print(f"=== {len(tr)} trades "
          f"({tr[0]['entry']:%Y-%m-%d} -> {tr[-1]['exit']:%Y-%m-%d}) ===\n")

    print("1) baseline by side / year")
    for side in ("LONG", "SHORT"):
        print(f"   {side:5s} {stats([t for t in tr if t['side'] == side])}")
    for y in (2024, 2025, 2026):
        sub = [t for t in tr if t["entry"].year == y]
        print(f"   {y}  {stats(sub)}")
        for side in ("LONG", "SHORT"):
            print(f"      {side:5s} {stats([t for t in sub if t['side']==side])}")

    print("\n2) exit-slippage anomalies (loss pct worse than -2.58 bracket)")
    for t in tr:
        if t["pct"] < -2.6 or (-2.5 < t["pct"] < 0):
            print(f"   #{t['n']:3d} {t['side']:5s} {t['entry']:%Y-%m-%d %H:%M} "
                  f"pct={t['pct']:+.2f} pnl=${t['pnl']:+.0f} bars={t['bars']}")

    print("\n3) hold time (bars) winners vs losers")
    for label, sub in (("win", [t for t in tr if t["pnl"] > 0]),
                       ("loss", [t for t in tr if t["pnl"] <= 0])):
        bars = sorted(t["bars"] for t in sub)
        med = bars[len(bars) // 2]
        print(f"   {label:4s} n={len(sub)} median={med} "
              f"p90={bars[int(len(bars)*0.9)]} max={bars[-1]}")
    quick = [t for t in tr if t["bars"] <= 6]
    print(f"   trades hitting an exit within 6 bars: {stats(quick)}")
    quick_l = [t for t in quick if t["pnl"] <= 0]
    print(f"     of which losses: {len(quick_l)} "
          f"(${sum(t['pnl'] for t in quick_l):+.0f}) — "
          "entered straight into a reversal")

    print("\n4) re-entry gap vs previous trade outcome")
    buckets = defaultdict(list)
    for i in range(1, len(tr)):
        gap_h = (tr[i]["entry"] - tr[i - 1]["exit"]).total_seconds() / 3600
        prev_loss = tr[i - 1]["pnl"] <= 0
        same = tr[i]["side"] == tr[i - 1]["side"]
        for lo, hi, name in ((0, 6, "<6h"), (6, 24, "6-24h"),
                             (24, 96, "1-4d"), (96, 1e9, ">4d")):
            if lo <= gap_h < hi:
                buckets[(name, prev_loss, same)].append(tr[i])
    print("   gap    after  side  " + " " * 14 + "performance")
    for name in ("<6h", "6-24h", "1-4d", ">4d"):
        for prev_loss in (True, False):
            for same in (True, False):
                sub = buckets.get((name, prev_loss, same))
                if sub:
                    print(f"   {name:6s} {'LOSS' if prev_loss else 'WIN ':4s}  "
                          f"{'same' if same else 'opp '}  {stats(sub)}")

    print("\n5) same-side loss chains (>=2 consecutive losses, same side)")
    chain, total_dmg = [], 0.0
    chains = []
    for t in tr:
        if t["pnl"] <= 0 and (not chain or (chain[-1]["side"] == t["side"])):
            chain.append(t)
        else:
            if len(chain) >= 2:
                chains.append(chain)
            chain = [t] if t["pnl"] <= 0 else []
    if len(chain) >= 2:
        chains.append(chain)
    for ch in chains:
        dmg = sum(t["pnl"] for t in ch)
        total_dmg += dmg
        print(f"   {ch[0]['entry']:%Y-%m-%d} {ch[0]['side']:5s} x{len(ch)} "
              f"${dmg:+.0f}  (trades {ch[0]['n']}-{ch[-1]['n']})")
    print(f"   total chain damage: ${total_dmg:+.0f}")

    print("\n6) risk rules simulated on the trade list (approximate)")
    base_eq, base_dd = equity_dd(tr)
    print(f"   baseline           : {stats(tr)}  endEq=${base_eq:,.0f} "
          f"maxDD={base_dd*100:.1f}%")

    # R1: v2 circuit breaker — 4 straight losses -> skip entries for 72h
    kept, streak, pause_until = [], 0, None
    for t in tr:
        if pause_until and t["entry"] < pause_until:
            continue
        kept.append(t)
        if t["pnl"] <= 0:
            streak += 1
            if streak >= 4:
                pause_until = t["exit"] + timedelta(hours=72)
                streak = 0
        else:
            streak = 0
    eq, dd = equity_dd(kept)
    print(f"   R1 breaker 4x/72h  : {stats(kept)}  endEq=${eq:,.0f} "
          f"maxDD={dd*100:.1f}%")

    # R2: same-side cooldown after a loss
    for cool_h in (6, 12, 24, 48):
        kept = []
        last_loss_exit = {}   # side -> exit dt of last losing trade
        for t in tr:
            ll = last_loss_exit.get(t["side"])
            if ll and (t["entry"] - ll).total_seconds() / 3600 < cool_h:
                continue
            kept.append(t)
            if t["pnl"] <= 0:
                last_loss_exit[t["side"]] = t["exit"]
            else:
                last_loss_exit.pop(t["side"], None)
        eq, dd = equity_dd(kept)
        print(f"   R2 cooldown {cool_h:2d}h    : {stats(kept)}  "
              f"endEq=${eq:,.0f} maxDD={dd*100:.1f}%")

    # R1+R2 combined (24h cooldown)
    kept, streak, pause_until, last_loss_exit = [], 0, None, {}
    for t in tr:
        if pause_until and t["entry"] < pause_until:
            continue
        ll = last_loss_exit.get(t["side"])
        if ll and (t["entry"] - ll).total_seconds() / 3600 < 24:
            continue
        kept.append(t)
        if t["pnl"] <= 0:
            last_loss_exit[t["side"]] = t["exit"]
            streak += 1
            if streak >= 4:
                pause_until = t["exit"] + timedelta(hours=72)
                streak = 0
        else:
            last_loss_exit.pop(t["side"], None)
            streak = 0
    eq, dd = equity_dd(kept)
    print(f"   R1 + R2(24h)       : {stats(kept)}  endEq=${eq:,.0f} "
          f"maxDD={dd*100:.1f}%")


if __name__ == "__main__":
    main()
