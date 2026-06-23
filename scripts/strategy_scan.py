"""Strategy scanner — 3 research-backed candidates on 12 months of real data.

WHY THIS EXISTS
    The live AVAX spectral-centroid strategy generated ~1 300 trades/year on
    30m bars and had a Profit Factor of 0.75-0.90 (see backtest.py).  The
    dominant loss driver is fee drag: 0.11% round-trip × 1 300 trades ≈ 143%
    of starting equity consumed in fees alone before any market edge counts.

    This scan tests three new strategies that use 4H bars (8× fewer bars →
    ~100-200 trades/year), making the fee burden manageable and giving edge
    room to breathe.

STRATEGIES
    S1  Donchian 20/10 Breakout (4H)
        Enter long when close > 20-bar highest high; short when < 20-bar
        lowest low.  Exit when price crosses back through the 10-bar channel.
        Classic "Turtle Trading" logic — works in trending crypto markets.

    S2  RSI(14) Pullback + EMA-200 Trend (4H)
        Only take trades in the direction of the dominant trend (price vs
        EMA-200).  Buy pullbacks when RSI crosses back above 35 from below
        (in uptrend); short rips when RSI crosses back below 65 from above
        (in downtrend).  ATR-based SL/TP.

    S3  MACD(12,26,9) Momentum + ATR Volatility Gate (4H)
        Enter on MACD-histogram zero-crosses, but only when current ATR is
        above its 20-bar median (= only trade when the market is moving, skip
        flat consolidation).  ATR-based SL/TP.

HOW TO RUN
    pip install ccxt pandas numpy
    python scripts/strategy_scan.py              # real data from Bybit
    python scripts/strategy_scan.py --synthetic  # offline, no network needed
    python scripts/strategy_scan.py --months 6  # 6 months instead of 12
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TAKER_FEE = 0.00055        # Bybit perp taker, per side
FUNDING_PER_8H = 0.0001    # ~0.01% / 8h (1 payment per 2 × 4H bars)
WARMUP = 250               # bars of 4H data to skip for indicator warmup
EQUITY_PCT = 0.40
SL_ATR_MULT = 1.5          # SL = 1.5 × ATR(14)
TP_ATR_MULT = 3.0          # TP = 3.0 × ATR(14)  →  2:1 risk/reward
TIME_EXIT_BARS = 120       # max hold = 120 × 4H = 20 days


# ── indicators ────────────────────────────────────────────────────────────────

def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _rsi(s: pd.Series, period: int = 14) -> pd.Series:
    d = s.diff()
    gain = d.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(span=period, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, 1e-10))


# ── data ──────────────────────────────────────────────────────────────────────

def fetch_real(months: int) -> pd.DataFrame:
    import ccxt
    ex = ccxt.bybit({
        "enableRateLimit": True,
        "options": {"defaultType": "linear", "fetchMarkets": {"types": ["linear"]}},
    })
    ex.load_markets()
    tf_ms = ex.parse_timeframe("30m") * 1000
    need = int(months * 30 * 24 * 60 * 60 * 1000 / tf_ms)
    since = ex.milliseconds() - need * tf_ms
    rows: list = []
    while True:
        batch = ex.fetch_ohlcv("AVAX/USDT:USDT", timeframe="30m", since=since, limit=1000)
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + tf_ms
        if len(batch) < 1000 or since >= ex.milliseconds():
            break
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("ts")
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("ts").sort_index()


def gen_synthetic(months: int) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    bars = int(months * 30 * 48)
    seg = bars // 4
    parts = []
    for drift in [0.0006, 0.0, -0.0005, 0.0]:
        if drift == 0.0:
            x = np.zeros(seg)
            for i in range(1, seg):
                x[i] = 0.90 * x[i - 1] + rng.normal(0, 0.005)
            r = np.diff(np.concatenate([[0], x]))
        else:
            r = rng.normal(drift, 0.006, seg)
        parts.append(r)
    r = np.concatenate(parts)
    close = pd.Series(20 * np.exp(np.cumsum(r)))
    high = close * (1 + np.abs(rng.normal(0, 0.003, len(close))))
    low = close * (1 - np.abs(rng.normal(0, 0.003, len(close))))
    idx = pd.date_range("2025-01-01", periods=len(close), freq="30min", tz="UTC")
    return pd.DataFrame(
        {"open": close.shift(1).fillna(close).values,
         "high": high.values, "low": low.values,
         "close": close.values, "volume": 1.0},
        index=idx,
    )


def to_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 30m OHLCV bars into 4H bars (causal — no future leak)."""
    return (
        df.resample("4h").agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .dropna(subset=["close"])
    )


# ── signal generators ─────────────────────────────────────────────────────────

def signals_donchian(df4: pd.DataFrame, entry_win: int = 20, exit_win: int = 10) -> pd.DataFrame:
    """
    Donchian channel breakout (classic Turtle Trading).
    Uses shift(1) on rolling min/max so we never peek at the current bar.
    """
    prev_h = df4["high"].shift(1).rolling(entry_win).max()
    prev_l = df4["low"].shift(1).rolling(entry_win).min()
    exit_h = df4["high"].shift(1).rolling(exit_win).max()
    exit_l = df4["low"].shift(1).rolling(exit_win).min()
    return pd.DataFrame(
        {
            "long_in":   (df4["close"] > prev_h) & (df4["close"].shift(1) <= prev_h.shift(1)),
            "short_in":  (df4["close"] < prev_l) & (df4["close"].shift(1) >= prev_l.shift(1)),
            "long_out":  df4["close"] < exit_l,
            "short_out": df4["close"] > exit_h,
        },
        index=df4.index,
    )


def signals_rsi_pullback(df4: pd.DataFrame) -> pd.DataFrame:
    """
    RSI(14) pullback entries aligned with EMA-200 trend.
    Long: price > EMA-200 and RSI crosses back above 35 from below.
    Short: price < EMA-200 and RSI crosses back below 65 from above.
    ATR SL/TP handles exits (no exit signal needed — return False everywhere).
    """
    r = _rsi(df4["close"])
    ema200 = _ema(df4["close"], 200)
    uptrend = df4["close"] > ema200
    downtrend = df4["close"] < ema200
    rsi_cross_up = (r >= 35) & (r.shift(1) < 35)
    rsi_cross_dn = (r <= 65) & (r.shift(1) > 65)
    false = pd.Series(False, index=df4.index)
    return pd.DataFrame(
        {
            "long_in":  uptrend & rsi_cross_up,
            "short_in": downtrend & rsi_cross_dn,
            "long_out":  false,
            "short_out": false,
        },
        index=df4.index,
    )


def signals_macd_momentum(df4: pd.DataFrame) -> pd.DataFrame:
    """
    MACD(12,26,9) histogram zero-cross gated by ATR volatility filter.
    Only enters when current ATR > 20-bar median ATR (avoids flat chop).
    ATR SL/TP handles exits.
    """
    macd = _ema(df4["close"], 12) - _ema(df4["close"], 26)
    hist = macd - _ema(macd, 9)
    a = _atr(df4)
    volatile = a > a.rolling(20).median()
    cross_up = (hist > 0) & (hist.shift(1) <= 0)
    cross_dn = (hist < 0) & (hist.shift(1) >= 0)
    false = pd.Series(False, index=df4.index)
    return pd.DataFrame(
        {
            "long_in":  cross_up & volatile,
            "short_in": cross_dn & volatile,
            "long_out":  false,
            "short_out": false,
        },
        index=df4.index,
    )


# ── simulator ─────────────────────────────────────────────────────────────────

def simulate(
    df4: pd.DataFrame,
    sigs: pd.DataFrame,
    label: str,
    start_equity: float = 1000.0,
    use_channel_exits: bool = False,
) -> dict:
    """
    Bar-by-bar simulation on 4H bars.

    Exit priority (highest to lowest):
      1. Intrabar SL or TP touch (modelled on bar's high/low)
      2. Channel exit signal (long_out / short_out) — only when use_channel_exits=True
      3. Time exit (TIME_EXIT_BARS)

    Anti-martingale sizing is intentionally disabled here so every strategy is
    judged on an identical 40%-equity-per-trade basis.
    """
    close = df4["close"].values
    high = df4["high"].values
    low = df4["low"].values
    atr = _atr(df4).values

    equity = start_equity
    side = 0            # +1 long, -1 short, 0 flat
    entry = sl = tp = 0.0
    qty = 0.0
    bars_held = 0
    max_eq = equity
    max_dd = 0.0
    trades_pnl: list[float] = []
    curve: list[float] = []

    for i in range(WARMUP, len(df4)):
        price = close[i]

        # funding charge every other 4H bar (= every 8h period)
        if side != 0 and i % 2 == 0:
            equity -= FUNDING_PER_8H * qty * price

        # 1) intrabar SL/TP
        if side != 0:
            hit: float | None = None
            if side == 1:
                if low[i] <= sl:   hit = sl
                elif high[i] >= tp: hit = tp
            else:
                if high[i] >= sl:  hit = sl
                elif low[i] <= tp:  hit = tp
            if hit is not None:
                pnl = (hit - entry) * qty * side
                pnl -= TAKER_FEE * qty * (entry + hit)
                equity += pnl
                trades_pnl.append(pnl)
                max_eq = max(max_eq, equity)
                max_dd = max(max_dd, (max_eq - equity) / max_eq)
                side = 0; bars_held = 0

        # 2) channel / signal exit
        if side != 0 and use_channel_exits:
            do_exit = (side == 1 and bool(sigs["long_out"].iloc[i])) or \
                      (side == -1 and bool(sigs["short_out"].iloc[i]))
            if do_exit:
                pnl = (price - entry) * qty * side
                pnl -= TAKER_FEE * qty * (entry + price)
                equity += pnl
                trades_pnl.append(pnl)
                max_eq = max(max_eq, equity)
                max_dd = max(max_dd, (max_eq - equity) / max_eq)
                side = 0; bars_held = 0

        # 3) time exit
        if side != 0 and bars_held >= TIME_EXIT_BARS:
            pnl = (price - entry) * qty * side
            pnl -= TAKER_FEE * qty * (entry + price)
            equity += pnl
            trades_pnl.append(pnl)
            max_eq = max(max_eq, equity)
            max_dd = max(max_dd, (max_eq - equity) / max_eq)
            side = 0; bars_held = 0

        if side != 0:
            bars_held += 1

        # entry
        if side == 0:
            want = 0
            if bool(sigs["long_in"].iloc[i]):   want = 1
            elif bool(sigs["short_in"].iloc[i]): want = -1
            if want != 0 and equity > 0 and not np.isnan(atr[i]):
                a = atr[i]
                qty = equity * EQUITY_PCT / price
                equity -= TAKER_FEE * qty * price   # entry fee
                entry = price
                sl = price - SL_ATR_MULT * a if want == 1 else price + SL_ATR_MULT * a
                tp = price + TP_ATR_MULT * a if want == 1 else price - TP_ATR_MULT * a
                side = want; bars_held = 0

        # mark-to-market for drawdown curve
        mtm = equity + (price - entry) * qty * side if side != 0 else equity
        curve.append(mtm)
        max_eq = max(max_eq, mtm)
        max_dd = max(max_dd, (max_eq - mtm) / max_eq)

    # close any open position at end of data
    if side != 0:
        price = close[-1]
        pnl = (price - entry) * qty * side
        pnl -= TAKER_FEE * qty * (entry + price)
        equity += pnl
        trades_pnl.append(pnl)

    n = len(trades_pnl)
    wins = [p for p in trades_pnl if p > 0]
    losses = [p for p in trades_pnl if p <= 0]
    gw = sum(wins)
    gl = abs(sum(losses)) or 1e-10
    pf = gw / gl
    wr = len(wins) / n * 100 if n else 0.0
    net_pct = (equity / start_equity - 1) * 100

    bars_total = len(df4) - WARMUP
    bars_per_year = 365 * 6   # 6 × 4H bars per day
    trades_yr = int(n * bars_per_year / max(bars_total, 1))

    return {
        "label": label,
        "trades": n,
        "trades_yr": trades_yr,
        "win_rate": wr,
        "profit_factor": pf,
        "net_pct": net_pct,
        "max_dd": max_dd * 100,
        "avg_win":  float(np.mean(wins)) if wins else 0.0,
        "avg_loss": float(np.mean(losses)) if losses else 0.0,
        "final_equity": equity,
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--equity", type=float, default=1000.0)
    ap.add_argument("--synthetic", action="store_true",
                    help="Use synthetic data (no network needed)")
    args = ap.parse_args()

    print("="*72)
    print("  STRATEGY SCANNER — AVAX/USDT:USDT — 4H BARS")
    print("="*72)

    if args.synthetic:
        df30 = gen_synthetic(args.months)
        src = f"SYNTHETIC ({args.months} months, NOT real AVAX)"
    else:
        try:
            print(f"Fetching {args.months} months of 30m bars from Bybit…")
            df30 = fetch_real(args.months)
            src = f"Bybit AVAX/USDT:USDT 30m → 4H  ({args.months} months)"
        except Exception as e:
            print(f"!! fetch failed ({type(e).__name__}: {str(e)[:80]})")
            print("   Falling back to synthetic data — run locally for real results.\n")
            df30 = gen_synthetic(args.months)
            src = f"SYNTHETIC fallback ({args.months} months)"

    df4 = to_4h(df30)
    span = f"{df4.index[0]:%Y-%m-%d} → {df4.index[-1]:%Y-%m-%d}"
    print(f"DATA: {src}")
    print(f"      {len(df30)} × 30m bars  →  {len(df4)} × 4H bars  |  {span}")
    print(f"      Start equity: ${args.equity:,.2f}\n")

    strategies = [
        ("S1 — Donchian 20/10 Breakout",         signals_donchian,      True),
        ("S2 — RSI(14) Pullback + EMA-200 Trend", signals_rsi_pullback,  False),
        ("S3 — MACD(12,26,9) + ATR Vol Gate",    signals_macd_momentum, False),
    ]

    results = []
    for label, sig_fn, channel_exits in strategies:
        sigs = sig_fn(df4)
        r = simulate(df4, sigs, label,
                     start_equity=args.equity,
                     use_channel_exits=channel_exits)
        results.append(r)

    # ── print table ──────────────────────────────────────────────────────────
    print(f"\n{'Strategy':<42} {'Trd':>4} {'~Yr':>4} {'Win%':>6} {'PF':>5}"
          f" {'Net%':>7} {'MaxDD%':>7}  Edge?")
    print("─" * 84)
    for r in sorted(results, key=lambda x: -x["profit_factor"]):
        edge = "POSITIVE ✓" if r["profit_factor"] > 1.0 else "negative ✗"
        print(
            f"  {r['label']:<40} {r['trades']:4d} {r['trades_yr']:4d}"
            f" {r['win_rate']:6.1f} {r['profit_factor']:5.3f}"
            f" {r['net_pct']:+7.1f} {r['max_dd']:7.1f}  {edge}"
        )
    print("─" * 84)
    print("PF > 1.0 = profitable (gross wins > gross losses, after fees)\n")

    # ── winner recommendation ─────────────────────────────────────────────────
    best = max(results, key=lambda x: x["profit_factor"])
    print("RECOMMENDATION")
    print("─" * 72)
    if best["profit_factor"] > 1.0:
        print(f"  Winner: {best['label']}")
        print(f"  PF {best['profit_factor']:.3f}  |  Win% {best['win_rate']:.1f}  |"
              f"  ~{best['trades_yr']} trades/year  |  MaxDD {best['max_dd']:.1f}%")
        print()
        print("  Next steps:")
        print("  1. Run on real data if you used --synthetic above")
        print("  2. python scripts/deploy_strategy.py --strategy <S1|S2|S3>")
        print("     (configures config/live.yaml and pushes the change)")
    else:
        print(f"  No strategy shows positive edge on this dataset.")
        print(f"  Best PF was {best['profit_factor']:.3f} ({best['label']}).")
        print(f"  Consider: longer lookback, different asset, or different timeframe.")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
