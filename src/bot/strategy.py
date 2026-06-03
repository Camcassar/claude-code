"""AVAX V-H Spectral strategy — Python port of the Pine Script.

Signal logic:
  1. Spectral centroid across 5 EMA octave bands — only trade when trending.
  2. EMA(20)/EMA(60) crossover for direction.
  3. Fixed 7% TP / 2.5% SL exits.
  4. Anti-martingale sizing: 3x base on 2+ consecutive wins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd


@dataclass
class Signal:
    action: Literal["buy", "sell", "short", "cover", "hold"]
    qty: float
    price: float
    sl_price: float
    tp_price: float
    centroid: float
    is_trending: bool


@dataclass
class StrategyState:
    win_streak: int = 0
    consec_3x: int = 0
    position: Literal["long", "short", "flat"] = "flat"
    entry_price: float = 0.0

    def on_close(self, pnl: float) -> None:
        self.position = "flat"
        self.entry_price = 0.0
        if pnl > 0:
            self.win_streak += 1
        else:
            self.win_streak = 0
            self.consec_3x = 0


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _spectral_centroid(close: pd.Series, pow_win: int = 50) -> pd.Series:
    b = [_ema(close, 2**i) - _ema(close, 2 ** (i + 1)) for i in range(1, 6)]
    p = [(band**2).rolling(pow_win).mean().clip(lower=1e-12) for band in b]
    periods = [8.0, 16.0, 32.0, 64.0, 128.0]
    centroid = sum(w * pi for w, pi in zip(periods, p)) / sum(p)
    return centroid


class AvaxSpectralStrategy:
    def __init__(
        self,
        equity_pct: float = 0.40,
        tc_thresh: float = 45.0,
        ema_fast: int = 20,
        ema_slow: int = 60,
        sl_pct: float = 2.5,
        tp_pct: float = 7.0,
        use_shorts: bool = True,
        am_mult_max: float = 2.0,
        am_mult_fallback: float = 1.5,
        am_stk_min: int = 2,
        max_consec_3x: int = 1,
    ) -> None:
        self.equity_pct = equity_pct
        self.tc_thresh = tc_thresh
        self.ema_fast_p = ema_fast
        self.ema_slow_p = ema_slow
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct
        self.use_shorts = use_shorts
        self.am_mult_max = am_mult_max
        self.am_mult_fallback = am_mult_fallback
        self.am_stk_min = am_stk_min
        self.max_consec_3x = max_consec_3x
        self.state = StrategyState()

    def evaluate(self, df: pd.DataFrame, equity: float = 130.0) -> Signal:
        """Evaluate on a dataframe of at least 300 closed 30m bars."""
        close = df["close"]
        price = float(close.iloc[-1])

        centroid = _spectral_centroid(close)
        c_val = float(centroid.iloc[-1])
        is_trending = c_val > self.tc_thresh

        ema_f = _ema(close, self.ema_fast_p)
        ema_s = _ema(close, self.ema_slow_p)
        cross_up = float(ema_f.iloc[-1]) > float(ema_s.iloc[-1]) and float(ema_f.iloc[-2]) <= float(ema_s.iloc[-2])
        cross_dn = float(ema_f.iloc[-1]) < float(ema_s.iloc[-1]) and float(ema_f.iloc[-2]) >= float(ema_s.iloc[-2])

        atr_val = float(_atr(df).iloc[-1])
        norm_atr = atr_val / price
        ref_atr = float(close.pct_change().abs().rolling(200).mean().iloc[-1])
        vol_mult = float(np.clip(norm_atr / max(ref_atr, 1e-10), 1.0, 1.5))

        streak_active = self.state.win_streak >= self.am_stk_min
        if streak_active and self.state.consec_3x < self.max_consec_3x:
            am_mult = self.am_mult_max
        elif streak_active:
            am_mult = self.am_mult_fallback
        else:
            am_mult = 1.0

        eff_cash = equity * self.equity_pct * vol_mult * am_mult
        qty = eff_cash / price

        pos = self.state.position
        go_long = cross_up and is_trending
        go_short = cross_dn and is_trending and self.use_shorts

        if go_long and pos != "long":
            action: Literal["buy", "sell", "short", "cover", "hold"] = "buy" if pos == "flat" else "buy"
            if pos == "short":
                action = "cover"
            sl = price * (1 - self.sl_pct / 100)
            tp = price * (1 + self.tp_pct / 100)
        elif go_short and pos != "short":
            action = "short" if pos == "flat" else "short"
            if pos == "long":
                action = "sell"
            sl = price * (1 + self.sl_pct / 100)
            tp = price * (1 - self.tp_pct / 100)
        else:
            action = "hold"
            sl = tp = 0.0

        return Signal(
            action=action,
            qty=qty,
            price=price,
            sl_price=sl,
            tp_price=tp,
            centroid=c_val,
            is_trending=is_trending,
        )
