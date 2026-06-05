"""
Smoke-test the FULL order path against Bybit — proof that the bot can trade.

Places ONE small market order with SL/TP, confirms the position actually
opened, then closes it. It uses the real production connector
(enter_long -> fetch_position -> close_position), so a success here means the
live bot's order path works end to end.

DEFAULT — testnet (paper money, zero risk). Needs testnet keys from
https://testnet.bybit.com  set as BYBIT_TESTNET_API_KEY / BYBIT_TESTNET_API_SECRET
(falls back to your normal keys if those aren't set):

    python scripts/force_trade.py

REAL account — places a tiny REAL order with your live keys:

    FORCE_TRADE_LIVE_CONFIRMED=1 python scripts/force_trade.py --live --qty 0.1
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv
import yaml

load_dotenv(PROJECT_ROOT / ".env")

from bot.exchange import BybitConnector  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description="Bybit order-path smoke test")
    ap.add_argument("--live", action="store_true", help="use REAL account (real money) instead of testnet")
    ap.add_argument("--qty", type=float, default=0.1, help="order size in AVAX (default 0.1)")
    args = ap.parse_args()

    cfg = yaml.safe_load((PROJECT_ROOT / "config" / "live.yaml").read_text())
    symbol = cfg["exchange"]["symbol"]
    leverage = cfg["exchange"].get("leverage", 3)

    if args.live:
        if os.getenv("FORCE_TRADE_LIVE_CONFIRMED") != "1":
            print("Refusing --live without FORCE_TRADE_LIVE_CONFIRMED=1 (this places a REAL order).", file=sys.stderr)
            return 2
        key = os.environ["BYBIT_API_KEY"]
        secret = os.environ["BYBIT_API_SECRET"]
        mode = "LIVE (real money)"
    else:
        key = os.getenv("BYBIT_TESTNET_API_KEY") or os.environ["BYBIT_API_KEY"]
        secret = os.getenv("BYBIT_TESTNET_API_SECRET") or os.environ["BYBIT_API_SECRET"]
        mode = "TESTNET (paper)"

    print("=" * 60)
    print(f"  ORDER-PATH SMOKE TEST  |  {mode}  |  {symbol}  |  {args.qty} AVAX")
    print("=" * 60)

    conn = BybitConnector(key, secret, symbol=symbol, leverage=leverage, testnet=not args.live)

    try:
        print("\n[1] Connecting + verifying auth ...")
        await conn.connect()
        bal = await conn.fetch_balance()
        print(f"    OK — balance ${bal:.2f} USDT")

        print("\n[2] Reading price ...")
        df = await conn.fetch_ohlcv(limit=5)
        price = float(df["close"].iloc[-1])
        # SL/TP placed far away (20%) so the test order won't get triggered out
        sl = round(price * 0.80, 4)
        tp = round(price * 1.20, 4)
        print(f"    price ${price:.4f}  |  SL ${sl}  TP ${tp}")

        print(f"\n[3] Placing market BUY {args.qty} AVAX ...")
        await conn.enter_long(args.qty, sl, tp)
        await asyncio.sleep(2)

        print("\n[4] Confirming the position opened ...")
        pos = await conn.fetch_position()
        print(f"    side={pos.side}  qty={pos.qty}  entry=${pos.entry_price:.4f}")
        if pos.side != "long" or pos.qty <= 0:
            print("    ✗ Position did NOT open as expected.", file=sys.stderr)
            return 1

        print("\n[5] Closing the position ...")
        await conn.close_position("long", pos.qty)
        await asyncio.sleep(2)
        pos2 = await conn.fetch_position()
        print(f"    side={pos2.side}  qty={pos2.qty}")
        if pos2.side != "none":
            print("    ⚠ Position may not be fully closed — CHECK BYBIT MANUALLY.", file=sys.stderr)
            return 1

        print("\n" + "=" * 60)
        print("  ✅ SUCCESS — the bot opened and closed a position end to end.")
        print("=" * 60)
        return 0
    except Exception as e:
        print(f"\n✗ FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
