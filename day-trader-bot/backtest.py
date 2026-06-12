"""
Backtest — replay the ORB+VWAP strategy over historical Bybit candles.

Usage:
    python backtest.py                 # fetch live history from Bybit (public API, no keys)
    python backtest.py --csv data.csv  # or replay from a CSV (ts,open,high,low,close,volume)

Simulates the exact same logic as bot.py: opening range per session,
VWAP + volume filters, SL at opposite range side, TP at 2R,
breakeven at +1R (intracandle, conservative ordering: stop checked first),
time exit at session close. Position sizing: 1% risk on a $10k account.
"""

import argparse
import csv
import sys
from datetime import datetime, timezone, timedelta

import config
import strategy
from indicators import atr


START_EQUITY = 10_000.0


def fetch_history(days):
    """Pull 5m candles from Bybit public API (no auth needed)."""
    from pybit.unified_trading import HTTP
    http = HTTP(testnet=False)
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = end - days * 86_400_000
    out = []
    cursor = start
    step = 1000 * int(config.TIMEFRAME) * 60_000  # 1000 candles per call
    while cursor < end:
        r = http.get_kline(
            category=config.CATEGORY, symbol=config.SYMBOL,
            interval=config.TIMEFRAME, start=cursor,
            end=min(cursor + step, end), limit=1000,
        )
        rows = r["result"]["list"]
        for row in rows:
            out.append({
                "ts": int(row[0]), "open": float(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]),
            })
        cursor += step
    out = sorted({c["ts"]: c for c in out}.values(), key=lambda c: c["ts"])
    return list(out)


def load_csv(path):
    out = []
    with open(path) as f:
        for row in csv.DictReader(f):
            out.append({k: (int(row[k]) if k == "ts" else float(row[k]))
                        for k in ("ts", "open", "high", "low", "close", "volume")})
    return sorted(out, key=lambda c: c["ts"])


def run_backtest(candles):
    equity = START_EQUITY
    trades = []
    orange = None
    skipped = False
    open_trade = None  # dict: side, entry, stop, tp, qty, be_moved, session

    # 400-candle rolling window: plenty for ATR(14), vol(20) and a full
    # session of VWAP (6h = 72 x 5m), and keeps the replay O(n).
    for i in range(50, len(candles)):
        window = candles[max(0, i - 400): i + 1]
        c = window[-1]
        now = datetime.fromtimestamp(c["ts"] / 1000, tz=timezone.utc) + timedelta(
            minutes=int(config.TIMEFRAME))  # candle close time

        # ── manage open trade on this candle ──
        if open_trade:
            t = open_trade
            exited = None
            be_r = config.BREAKEVEN_AT_R
            if t["side"] == "Buy":
                r0 = t["entry"] - t["stop0"]
                if c["low"] <= t["stop"]:
                    exited = (t["stop"], "stop")
                elif c["high"] >= t["tp"]:
                    exited = (t["tp"], "tp")
                elif not t["be_moved"] and c["high"] >= t["entry"] + be_r * r0:
                    t["stop"], t["be_moved"] = t["entry"], True
            else:
                r0 = t["stop0"] - t["entry"]
                if c["high"] >= t["stop"]:
                    exited = (t["stop"], "stop")
                elif c["low"] <= t["tp"]:
                    exited = (t["tp"], "tp")
                elif not t["be_moved"] and c["low"] <= t["entry"] - be_r * r0:
                    t["stop"], t["be_moved"] = t["entry"], True
            if not exited and orange and c["ts"] >= orange.close_ms:
                exited = (c["close"], "time")
            if exited:
                px, why = exited
                direction = 1 if t["side"] == "Buy" else -1
                pnl = direction * (px - t["entry"]) * t["qty"]
                fees = 0.00055 * t["qty"] * (t["entry"] + px)  # taker both sides
                pnl -= fees
                equity += pnl
                trades.append({"session": t["session"], "side": t["side"],
                               "entry": t["entry"], "exit": px, "why": why,
                               "pnl": pnl, "ts": c["ts"]})
                open_trade = None

        # ── session / range management ──
        sess, sess_start = strategy.current_session(now)
        if sess is None:
            orange, skipped = None, False
            continue
        start_ms = int(sess_start.timestamp() * 1000)
        if orange is None or orange.start_ms != start_ms:
            orange = strategy.build_opening_range(window, sess, sess_start)
            skipped = False
            if orange:
                ok, _ = strategy.range_is_tradeable(orange, window)
                skipped = not ok
        if orange is None or skipped or open_trade:
            continue

        # one trade per side per session
        taken = {(t["session"], t["side"]) for t in trades}
        sig = strategy.check_breakout(window, orange)
        if sig and (f"{sess['name']}:{start_ms}", sig.side) not in taken:
            risk_amt = equity * (config.RISK_PCT / 100.0)
            dist = abs(sig.entry - sig.stop)
            if dist <= 0:
                continue
            qty = risk_amt / dist
            open_trade = {"side": sig.side, "entry": sig.entry, "stop": sig.stop,
                          "stop0": sig.stop, "tp": sig.take_profit, "qty": qty,
                          "be_moved": False, "session": f"{sess['name']}:{start_ms}"}

    return equity, trades


def report(equity, trades, days):
    print(f"\n=== Backtest: {config.SYMBOL} {config.TIMEFRAME}m, last {days} days ===")
    print(f"Start equity : ${START_EQUITY:,.2f}")
    print(f"End equity   : ${equity:,.2f}  ({(equity / START_EQUITY - 1) * 100:+.2f}%)")
    if not trades:
        print("No trades taken.")
        return
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    print(f"Trades       : {len(trades)}  (win rate {len(wins) / len(trades) * 100:.0f}%)")
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    print(f"Avg win      : ${gw / len(wins):,.2f}" if wins else "Avg win      : –")
    print(f"Avg loss     : ${gl / len(losses):,.2f}" if losses else "Avg loss     : –")
    print(f"Profit factor: {gw / gl:.2f}" if gl > 0 else "Profit factor: inf")
    by_exit = {}
    for t in trades:
        by_exit[t["why"]] = by_exit.get(t["why"], 0) + 1
    print(f"Exits        : {by_exit}")
    print("\nLast 10 trades:")
    for t in trades[-10:]:
        d = datetime.fromtimestamp(t["ts"] / 1000, tz=timezone.utc)
        print(f"  {d:%m-%d %H:%M} {t['side']:4} in {t['entry']:.3f} out {t['exit']:.3f} "
              f"({t['why']:4}) {t['pnl']:+8.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--csv", help="replay from CSV instead of fetching")
    args = ap.parse_args()

    candles = load_csv(args.csv) if args.csv else fetch_history(args.days)
    if not candles:
        sys.exit("No candle data.")
    days = (candles[-1]["ts"] - candles[0]["ts"]) / 86_400_000
    equity, trades = run_backtest(candles)
    report(equity, trades, round(days))
