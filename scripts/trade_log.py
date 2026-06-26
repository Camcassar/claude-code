"""
Trade log viewer — pulls live data from Bybit + local CSV.

Usage:
    python scripts/trade_log.py          # show all closed trades + current position
    python scripts/trade_log.py --limit 20
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from exchange import Bybit


def fmt_pnl(pnl: float) -> str:
    sign = "+" if pnl >= 0 else ""
    return f"{sign}{pnl:.4f} USDT"


def fmt_ts(ts_ms: int) -> str:
    import time
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
    pos = ex.get_position()
    equity = ex.get_equity()
    print(f"  Balance : {equity:.2f} USDT")
    if pos:
        direction = "LONG" if pos["side"] == "Buy" else "SHORT"
        pnl_str = fmt_pnl(pos["unrealized_pnl"])
        sl_str = f"{pos['stop_loss']:.2f}" if pos["stop_loss"] else "—"
        tp_str = f"{pos['take_profit']:.2f}" if pos.get("take_profit") else "—"
        print(f"  Position: {direction} {pos['size']} ETHUSDT")
        print(f"  Entry   : {pos['entry']:.2f}")
        print(f"  Unreal. : {pnl_str}")
        print(f"  TP      : {tp_str}")
        print(f"  SL      : {sl_str}")
    else:
        print("  Position: None (flat)")

    # ── Closed trades from Bybit ──────────────────────────────────────
    print(f"\n── Closed Trades (last {args.limit}) ─────────────────────")
    try:
        r = ex.http.get_closed_pnl(
            category=config.CATEGORY,
            symbol=config.SYMBOL,
            limit=args.limit,
        )
        trades = r["result"]["list"]
    except Exception as e:
        print(f"  Error fetching closed PnL: {e}")
        trades = []

    if not trades:
        print("  No closed trades found.")
    else:
        wins = losses = 0
        total_pnl = 0.0
        print(f"  {'#':<4} {'Closed':<20} {'Side':<6} {'Qty':<8} {'Entry':>8} {'Exit':>8} {'PnL':>12}")
        print("  " + "─" * 72)
        for i, t in enumerate(trades, 1):
            pnl = float(t["closedPnl"])
            side = "LONG" if t["side"] == "Buy" else "SHORT"
            qty = float(t["qty"])
            entry = float(t["avgEntryPrice"])
            exit_px = float(t["avgExitPrice"])
            closed_at = fmt_ts(int(t["updatedTime"]))
            total_pnl += pnl
            if pnl >= 0:
                wins += 1
            else:
                losses += 1
            marker = "✅" if pnl >= 0 else "❌"
            print(f"  {i:<4} {closed_at:<20} {side:<6} {qty:<8} {entry:>8.2f} {exit_px:>8.2f} {fmt_pnl(pnl):>12}  {marker}")

        print("  " + "─" * 72)
        total = wins + losses
        wr = (wins / total * 100) if total else 0
        print(f"  Total: {total} trades | W {wins} / L {losses} | Win rate {wr:.0f}% | Net PnL {fmt_pnl(total_pnl)}")

    # ── Local CSV log ─────────────────────────────────────────────────
    csv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "trades.csv")
    if os.path.exists(csv_path):
        import csv
        print(f"\n── Local Trade Log ({csv_path}) ──────────────────────────")
        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
        if not rows:
            print("  No entries yet.")
        else:
            for row in rows:
                if row["type"] == "ENTRY":
                    print(f"  ENTRY  {row['timestamp']}  {row['direction']:<6}  qty={row['qty']}  px={row['price']}  z={row['z_score']}  eq={row['equity']}")
                else:
                    print(f"  EXIT   {row['timestamp']}  pnl={row['pnl']}  streak={row['loss_streak']}")


if __name__ == "__main__":
    main()
