"""
Trade log viewer — live position + full Bybit history across all symbols.

Usage:
    python scripts/trade_log.py          # last 50 trades
    python scripts/trade_log.py --limit 100
"""

import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from exchange import Bybit


def fmt_pnl(pnl: float) -> str:
    sign = "+" if pnl >= 0 else ""
    return f"{sign}{pnl:.4f}"


def fmt_ts(ts_ms: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts_ms / 1000))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    if not config.API_KEY or not config.API_SECRET:
        sys.exit("No API keys found. Add BYBIT_API_KEY and BYBIT_API_SECRET to .env")

    ex = Bybit(symbol=config.SYMBOL)

    # ── Current position ─────────────────────────────────────────────
    print("\n── Current Position ──────────────────────────────────────")
    try:
        pos = ex.get_position()
        equity = ex.get_equity()
        print(f"  Balance : {equity:.2f} USDT")
        if pos:
            direction = "LONG" if pos["side"] == "Buy" else "SHORT"
            tp_str = f"{pos['take_profit']:.2f}" if pos.get("take_profit") else "—"
            sl_str = f"{pos['stop_loss']:.2f}" if pos.get("stop_loss") else "—"
            print(f"  Position: {direction} {pos['size']} {config.SYMBOL}")
            print(f"  Entry   : {pos['entry']:.2f}")
            print(f"  Unreal. : {fmt_pnl(pos['unrealized_pnl'])} USDT")
            print(f"  TP      : {tp_str}  |  SL: {sl_str}")
        else:
            print("  Position: None (flat)")
    except Exception as e:
        print(f"  Error fetching position: {e}")

    # ── Full trade history from Bybit ─────────────────────────────────
    print(f"\n── Closed Trades — All Symbols (last {args.limit}) ──────────")
    try:
        trades = ex.get_all_closed_pnl(limit=args.limit)
    except Exception as e:
        print(f"  Error fetching trades: {e}")
        trades = []

    if not trades:
        print("  No closed trades found.")
    else:
        wins = losses = 0
        total_pnl = 0.0
        by_symbol: dict = {}

        print(f"  {'#':<4} {'Closed':<20} {'Symbol':<10} {'Side':<6} {'Entry':>8} {'Exit':>8} {'PnL (USDT)':>12}")
        print("  " + "─" * 74)

        for i, t in enumerate(trades, 1):
            pnl = float(t["closedPnl"])
            side = "LONG" if t["side"] == "Buy" else "SHORT"
            sym = t["symbol"]
            entry = float(t["avgEntryPrice"])
            exit_px = float(t["avgExitPrice"])
            closed_at = fmt_ts(int(t["updatedTime"]))
            total_pnl += pnl
            marker = "✅" if pnl >= 0 else "❌"
            if pnl >= 0:
                wins += 1
            else:
                losses += 1
            by_symbol.setdefault(sym, {"wins": 0, "losses": 0, "pnl": 0.0})
            if pnl >= 0:
                by_symbol[sym]["wins"] += 1
            else:
                by_symbol[sym]["losses"] += 1
            by_symbol[sym]["pnl"] += pnl
            print(f"  {i:<4} {closed_at:<20} {sym:<10} {side:<6} {entry:>8.3f} {exit_px:>8.3f} {fmt_pnl(pnl):>12}  {marker}")

        print("  " + "─" * 74)
        total = wins + losses
        wr = (wins / total * 100) if total else 0
        print(f"  {total} trades | W {wins} / L {losses} | Win rate {wr:.0f}% | Net PnL {fmt_pnl(total_pnl)} USDT")

        if len(by_symbol) > 1:
            print("\n── By Symbol ─────────────────────────────────────────────")
            for sym, s in by_symbol.items():
                t2 = s["wins"] + s["losses"]
                wr2 = (s["wins"] / t2 * 100) if t2 else 0
                print(f"  {sym:<10} W {s['wins']} / L {s['losses']} | WR {wr2:.0f}% | PnL {fmt_pnl(s['pnl'])} USDT")

    # ── Local persistent CSV (Railway /data volume) ───────────────────
    csv_path = os.getenv("TRADE_LOG", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "trades.csv"
    ))
    if os.path.exists(csv_path):
        print(f"\n── Local Entry Log ({csv_path}) ──────────────────────────")
        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
        if not rows:
            print("  No entries yet.")
        else:
            print(f"  {'Time':<22} {'Type':<6} {'Dir':<6} {'Qty':<8} {'Price':>8} {'z-score':>8} {'Equity':>8} {'PnL':>10}")
            print("  " + "─" * 80)
            for row in rows:
                if row["type"] == "ENTRY":
                    print(f"  {row['timestamp']:<22} ENTRY  {row['direction']:<6} {row['qty']:<8} {row['price']:>8} {row['z_score']:>8} {row['equity']:>8}")
                else:
                    print(f"  {row['timestamp']:<22} EXIT   {'':6} {'':8} {'':>8} {'':>8} {'':>8} {row['pnl']:>10}  streak={row['loss_streak']}")
    else:
        print(f"\n  (No local entry log yet — will appear at {csv_path} after first trade)")


if __name__ == "__main__":
    main()
