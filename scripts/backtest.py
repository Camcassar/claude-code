"""Real-data backtest — does fixing the trend filter make Bot 8 profitable?

WHY THIS EXISTS
    The live "spectral centroid > 45" trend filter is mathematically broken:
    the longest-period EMA band dominates the weighted average, so the centroid
    sits at ~80-100 permanently and flags ~75-99% of ALL bars (even pure noise)
    as "trending". It therefore provides no protection against chop, which is
    where the strategy bleeds. This script measures, on REAL AVAX history,
    whether replacing it with a genuine trend gate (ADX) — and letting winners
    run with a trailing stop — turns the edge positive.

HOW TO RUN (needs network access to an exchange — run locally or on Railway)
    pip install ccxt pandas numpy
    python scripts/backtest.py                      # Bybit, AVAX/USDT, 30m, 12 months
    python scripts/backtest.py --months 6 --exchange binance
    python scripts/backtest.py --synthetic          # offline demo (labelled synthetic)

It prints a comparison table across strategy variants and writes equity curves
to logs/backtest_<variant>.csv.

NOTE: this sandbox where Claude runs has its egress firewalled, so Claude cannot
fetch real data itself. Run this on a machine that can reach the exchange and
paste back the table.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Reuse the EXACT causal indicator code the live bot uses, so the backtest can
# never silently diverge from production behaviour.
from bot.strategy import _ema, _atr, _spectral_centroid  # noqa: E402

TAKER_FEE = 0.00055        # Bybit perp taker, per side
FUNDING_PER_8H = 0.0001    # ~0.01% / 8h, applied to notional while in a position
WARMUP = 300               # bars needed before the first trade (EMA64/centroid/ref_atr)


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def fetch_real(exchange: str, symbol: str, timeframe: str, months: int) -> pd.DataFrame:
    """Paginated OHLCV pull from a ccxt exchange. Returns UTC-indexed OHLCV."""
    import ccxt  # imported here so --synthetic works without ccxt installed

    ex = getattr(ccxt, exchange)({"enableRateLimit": True, "options": {"defaultType": "linear"}})
    ex.load_markets()
    tf_ms = ex.parse_timeframe(timeframe) * 1000
    need = int(months * 30 * 24 * 60 * 60 * 1000 / tf_ms)
    since = ex.milliseconds() - need * tf_ms
    rows: list[list] = []
    while True:
        batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1000)
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + tf_ms
        if len(batch) < 1000 or since >= ex.milliseconds():
            break
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts")
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("ts").sort_index()


def gen_synthetic(months: int) -> pd.DataFrame:
    """Offline fallback: stitched trend/chop/trend-down/chop regimes (LABELLED)."""
    rng = np.random.default_rng(7)
    bars = int(months * 30 * 48)
    seg = bars // 4
    parts = []
    for drift, kind in [(0.0005, "up"), (0.0, "chop"), (-0.0005, "down"), (0.0, "chop")]:
        if kind == "chop":
            x = np.zeros(seg)
            for i in range(1, seg):
                x[i] = 0.92 * x[i - 1] + rng.normal(0, 0.005)
            r = np.diff(np.concatenate([[0], x]))
        else:
            r = rng.normal(drift, 0.005, seg)
        parts.append(r)
    r = np.concatenate(parts)
    close = pd.Series(20 * np.exp(np.cumsum(r)))
    high = close * (1 + np.abs(rng.normal(0, 0.0025, len(close))))
    low = close * (1 - np.abs(rng.normal(0, 0.0025, len(close))))
    idx = pd.date_range("2025-01-01", periods=len(close), freq="30min", tz="UTC")
    return pd.DataFrame({"open": close.shift(1).fillna(close).values,
                         "high": high.values, "low": low.values,
                         "close": close.values, "volume": 1.0}, index=idx)


# --------------------------------------------------------------------------- #
# Indicators / filters
# --------------------------------------------------------------------------- #
def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ADX — a genuine trend-strength gate (causal)."""
    h, l, c = df["high"], df["low"], df["close"]
    up, dn = h.diff(), -l.diff()
    plus_dm = ((up > dn) & (up > 0)) * up
    minus_dm = ((dn > up) & (dn > 0)) * dn
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean().replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


# --------------------------------------------------------------------------- #
# Simulator
# --------------------------------------------------------------------------- #
def simulate(df: pd.DataFrame, *, gate: str, exit_mode: str,
             equity_pct: float = 0.40, leverage: float = 3.0,
             sl_pct: float = 2.0, tp_pct: float = 3.5, trail_pct: float = 2.0,
             time_exit_bars: int = 24, adx_thresh: float = 25.0,
             tc_thresh: float = 45.0, start_equity: float = 100.0) -> dict:
    """Bar-by-bar sim. gate: 'centroid'(broken) | 'adx' | 'none'.
    exit_mode: 'fixed' (TP/SL) | 'trailing' (trailing stop, no fixed TP)."""
    close = df["close"]
    ema_f = _ema(close, 20).values
    ema_s = _ema(close, 60).values
    cent = _spectral_centroid(close).values
    atr = _atr(df).values
    ref_atr = close.pct_change().abs().rolling(200).mean().values
    adx_v = adx(df).values
    px = close.values
    hi, lo = df["high"].values, df["low"].values

    equity = start_equity
    side = 0            # +1 long, -1 short, 0 flat
    entry = stop = tp = hwm = 0.0
    qty = 0.0
    bars_held = 0
    win_streak = 0
    trades: list[float] = []
    curve = []

    def gate_ok(i: int) -> bool:
        if gate == "centroid":
            return cent[i] > tc_thresh
        if gate == "adx":
            return adx_v[i] > adx_thresh
        return True  # 'none'

    for i in range(WARMUP, len(df)):
        price = px[i]

        # 1) intrabar stop/target on the existing position (exchange-native)
        if side != 0:
            bars_held += 1
            exit_px = None
            if exit_mode == "trailing":
                if side == 1:
                    hwm = max(hwm, hi[i]); stop = max(stop, hwm * (1 - trail_pct / 100))
                    if lo[i] <= stop: exit_px = stop
                else:
                    hwm = min(hwm, lo[i]); stop = min(stop, hwm * (1 + trail_pct / 100))
                    if hi[i] >= stop: exit_px = stop
            else:  # fixed
                if side == 1:
                    if lo[i] <= stop: exit_px = stop
                    elif hi[i] >= tp: exit_px = tp
                else:
                    if hi[i] >= stop: exit_px = stop
                    elif lo[i] <= tp: exit_px = tp
            if exit_px is None and bars_held >= time_exit_bars:
                exit_px = price  # time exit
            if exit_px is not None:
                pnl = (exit_px - entry) * qty * side
                pnl -= TAKER_FEE * qty * (entry + exit_px)               # both sides
                pnl -= FUNDING_PER_8H * qty * entry * (bars_held / 16)   # 16 bars = 8h
                equity += pnl
                trades.append(pnl)
                win_streak = win_streak + 1 if pnl > 0 else 0
                side = 0; bars_held = 0

        # 2) desired exposure from filter + EMA alignment (decided on close[i])
        want = 0
        if gate_ok(i) and not np.isnan(ref_atr[i]):
            if ema_f[i] > ema_s[i]:
                want = 1
            elif ema_f[i] < ema_s[i]:
                want = -1

        # 3) flip if signal opposes the open position
        if side != 0 and want != 0 and want != side:
            pnl = (price - entry) * qty * side
            pnl -= TAKER_FEE * qty * (entry + price)
            pnl -= FUNDING_PER_8H * qty * entry * (bars_held / 16)
            equity += pnl; trades.append(pnl)
            win_streak = win_streak + 1 if pnl > 0 else 0
            side = 0; bars_held = 0

        # 4) open if flat and signalled
        if side == 0 and want != 0 and equity > 0:
            vol_mult = float(np.clip((atr[i] / price) / max(ref_atr[i], 1e-10), 1.0, 1.5))
            am_mult = 2.0 if win_streak >= 2 else 1.0
            notional = equity * equity_pct * vol_mult * am_mult
            qty = notional / price
            entry = price; side = want; bars_held = 0; hwm = price
            if exit_mode == "trailing":
                stop = price * (1 - trail_pct / 100) if side == 1 else price * (1 + trail_pct / 100)
                tp = 0.0
            else:
                stop = price * (1 - sl_pct / 100) if side == 1 else price * (1 + sl_pct / 100)
                tp = price * (1 + tp_pct / 100) if side == 1 else price * (1 - tp_pct / 100)

        # mark-to-market equity for the drawdown curve
        mtm = equity + ((price - entry) * qty * side if side != 0 else 0.0)
        curve.append(mtm)

    eq = pd.Series(curve, index=df.index[WARMUP:])
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    gross_w, gross_l = sum(wins), -sum(losses)
    dd = (eq / eq.cummax() - 1).min() * 100
    n = len(trades)
    return {
        "trades": n,
        "win_rate": (len(wins) / n * 100) if n else 0.0,
        "net_pct": (equity / start_equity - 1) * 100,
        "final": equity,
        "profit_factor": (gross_w / gross_l) if gross_l else float("inf"),
        "max_dd": dd,
        "avg_win": (np.mean(wins) if wins else 0.0),
        "avg_loss": (np.mean(losses) if losses else 0.0),
        "curve": eq,
    }


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exchange", default="bybit")
    ap.add_argument("--symbol", default="AVAX/USDT:USDT")
    ap.add_argument("--timeframe", default="30m")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--equity", type=float, default=100.0)
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    if args.synthetic:
        df = gen_synthetic(args.months)
        src = f"SYNTHETIC (labelled — NOT real AVAX) {args.months}mo"
    else:
        try:
            df = fetch_real(args.exchange, args.symbol, args.timeframe, args.months)
            src = f"{args.exchange} {args.symbol} {args.timeframe}"
        except Exception as e:
            print(f"!! real fetch failed ({type(e).__name__}: {str(e)[:80]}); "
                  f"falling back to --synthetic\n")
            df = gen_synthetic(args.months)
            src = f"SYNTHETIC fallback (real fetch blocked) {args.months}mo"

    span = f"{df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d}"
    print(f"DATA: {src} | {len(df)} bars | {span}\n")

    variants = [
        ("V0 live (broken centroid, fixed TP/SL)", dict(gate="centroid", exit_mode="fixed")),
        ("V1 ADX gate, fixed TP/SL",               dict(gate="adx",      exit_mode="fixed")),
        ("V2 ADX gate, trailing stop",             dict(gate="adx",      exit_mode="trailing")),
        ("V3 no gate, fixed TP/SL (control)",      dict(gate="none",     exit_mode="fixed")),
    ]
    print(f"{'variant':42s} {'trades':>6} {'win%':>6} {'net%':>8} {'PF':>5} {'maxDD%':>7}")
    print("-" * 80)
    (ROOT / "logs").mkdir(exist_ok=True)
    for name, kw in variants:
        r = simulate(df, start_equity=args.equity, **kw)
        print(f"{name:42s} {r['trades']:6d} {r['win_rate']:6.1f} "
              f"{r['net_pct']:+8.1f} {r['profit_factor']:5.2f} {r['max_dd']:7.1f}")
        r["curve"].to_csv(ROOT / "logs" / f"backtest_{kw['gate']}_{kw['exit_mode']}.csv")
    print("-" * 80)
    print("PF = profit factor (gross wins / gross losses; >1 = profitable). "
          "Equity curves saved to logs/backtest_*.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
