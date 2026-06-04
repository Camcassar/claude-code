"""
Dry-run: connects to real Bybit, fetches live AVAX data, runs the strategy.
Prints exactly what the bot would do. Places NO orders.

Run: python scripts/dry_run.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv
import yaml

load_dotenv(PROJECT_ROOT / ".env")

import ccxt.async_support as ccxt
import pandas as pd


def _ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=p, adjust=False).mean()


async def main() -> None:
    cfg = yaml.safe_load((PROJECT_ROOT / "config" / "live.yaml").read_text())
    symbol = cfg["exchange"]["symbol"]
    strat = cfg["strategy"]
    sizing = cfg["sizing"]

    api_key = os.environ["BYBIT_API_KEY"]
    api_secret = os.environ["BYBIT_API_SECRET"]

    ex = ccxt.bybit({
        "apiKey": api_key,
        "secret": api_secret,
        "options": {"defaultType": "linear"},
        "enableRateLimit": True,
    })

    print("=" * 60)
    print("BOT 8 — AVAX SPECTRAL  |  DRY RUN")
    print("=" * 60)

    # ── 1. Connect (with our geo-block fix) ──────────────────────
    print("\n[1] Connecting to Bybit...")
    ex.has["fetchCurrencies"] = False
    try:
        await ex.load_markets()
        print("    ✓ Connected OK")
    except Exception as e:
        print(f"    ✗ Connection FAILED: {e}")
        await ex.close()
        return

    # ── 2. Balance ───────────────────────────────────────────────
    print("\n[2] Fetching balance...")
    try:
        bal = await ex.fetch_balance({"type": "unified"})
        usdt_free = float(bal.get("USDT", {}).get("free", 0.0))
        usdt_total = float(bal.get("USDT", {}).get("total", 0.0))
        print(f"    Free USDT:  ${usdt_free:.2f}")
        print(f"    Total USDT: ${usdt_total:.2f}")
    except Exception as e:
        print(f"    ✗ Balance FAILED: {e}")
        await ex.close()
        return

    # ── 3. Current position ──────────────────────────────────────
    print(f"\n[3] Fetching position for {symbol}...")
    try:
        positions = await ex.fetch_positions([symbol])
        pos_side = "none"
        pos_qty = 0.0
        pos_entry = 0.0
        pos_upnl = 0.0
        for p in positions:
            if p["symbol"] == symbol and float(p.get("contracts", 0) or 0) > 0:
                pos_side = p["side"].lower()
                pos_qty = float(p["contracts"])
                pos_entry = float(p["entryPrice"] or 0)
                pos_upnl = float(p["unrealizedPnl"] or 0)
        if pos_side == "none":
            print("    No open position (flat)")
        else:
            print(f"    Side:       {pos_side.upper()}")
            print(f"    Qty:        {pos_qty} AVAX")
            print(f"    Entry:      ${pos_entry:.4f}")
            print(f"    Unrealised: ${pos_upnl:+.4f}")
    except Exception as e:
        print(f"    ✗ Position FAILED: {e}")
        await ex.close()
        return

    # ── 4. OHLCV ─────────────────────────────────────────────────
    print(f"\n[4] Fetching 350 x 30m bars for {symbol}...")
    try:
        raw = await ex.fetch_ohlcv(symbol, timeframe="30m", limit=350)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp")
        df = df.iloc[:-1]  # drop in-progress bar
        print(f"    ✓ Got {len(df)} closed bars")
        print(f"    Latest bar:  {df.index[-1].strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"    Close price: ${float(df['close'].iloc[-1]):.4f}")
    except Exception as e:
        print(f"    ✗ OHLCV FAILED: {e}")
        await ex.close()
        return

    # ── 5. Strategy evaluation ───────────────────────────────────
    print("\n[5] Running strategy evaluation...")
    try:
        from bot.strategy import AvaxSpectralStrategy
        strategy = AvaxSpectralStrategy(
            equity_pct=sizing["equity_pct"],
            tc_thresh=strat["tc_thresh"],
            ema_fast=strat["ema_fast"],
            ema_slow=strat["ema_slow"],
            sl_pct=strat["sl_pct"],
            tp_pct=strat["tp_pct"],
            use_shorts=strat["use_shorts"],
            am_mult_max=sizing["am_mult_max"],
            am_mult_fallback=sizing["am_mult_fallback"],
            am_stk_min=sizing["am_streak_min"],
            max_consec_3x=sizing["max_consec_3x"],
        )
        sig = strategy.evaluate(df, equity=usdt_free)

        trending_str = "TRENDING" if sig.is_trending else "CHOPPY"
        print(f"    Centroid:    {sig.centroid:.2f}  (threshold: {strat['tc_thresh']})")
        print(f"    Market:      {trending_str}")
        print(f"    EMA fast/slow cross: ", end="")

        close = df["close"]
        ema_f = close.ewm(span=strat["ema_fast"], adjust=False).mean()
        ema_s = close.ewm(span=strat["ema_slow"], adjust=False).mean()
        cross_up = float(ema_f.iloc[-1]) > float(ema_s.iloc[-1]) and float(ema_f.iloc[-2]) <= float(ema_s.iloc[-2])
        cross_dn = float(ema_f.iloc[-1]) < float(ema_s.iloc[-1]) and float(ema_f.iloc[-2]) >= float(ema_s.iloc[-2])
        ema_bias = "bullish" if float(ema_f.iloc[-1]) > float(ema_s.iloc[-1]) else "bearish"
        cross_str = "UP CROSS" if cross_up else ("DOWN CROSS" if cross_dn else f"no cross ({ema_bias} bias)")
        print(cross_str)

        action_display = {
            "buy": "  ➡  BUY (enter LONG)",
            "short": "  ➡  SHORT (enter SHORT)",
            "sell": "  ➡  SELL (exit LONG → SHORT)",
            "cover": "  ➡  COVER (exit SHORT → LONG)",
            "hold": "  ➡  HOLD (no trade)",
        }
        print(f"\n    SIGNAL:      {action_display.get(sig.action, sig.action.upper())}")

        if sig.action != "hold":
            print(f"    Price:       ${sig.price:.4f}")
            print(f"    Qty:         {sig.qty:.4f} AVAX")
            print(f"    Stop loss:   ${sig.sl_price:.4f}")
            print(f"    Take profit: ${sig.tp_price:.4f}")
            notional = sig.qty * sig.price
            print(f"    Notional:    ${notional:.2f} USDT")
        else:
            print("    (waiting for EMA cross in a trending market)")

    except Exception as e:
        print(f"    ✗ Strategy FAILED: {e}")
        import traceback; traceback.print_exc()
        await ex.close()
        return

    # ── 6. Loop timing check ─────────────────────────────────────
    print("\n[6] Loop timing...")
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    bar_seconds = 30 * 60
    remainder = now.timestamp() % bar_seconds
    wait = (bar_seconds - remainder) + 10  # +10s bar delay
    mins, secs = divmod(int(wait), 60)
    print(f"    Current UTC: {now.strftime('%H:%M:%S')}")
    print(f"    Next tick in: {mins}m {secs}s")

    await ex.close()

    print("\n" + "=" * 60)
    print("ALL CHECKS PASSED — bot would run fine from this machine.")
    print("Railway geo-block fix (fetchCurrencies=False) is applied.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
