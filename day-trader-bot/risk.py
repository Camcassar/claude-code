"""
Risk management — position sizing and daily loss limit.
"""

import json
import math
import os
from datetime import datetime, timezone

import config


def position_size(equity, entry, stop, qty_step, min_qty):
    """Risk-based sizing: risk RISK_PCT of equity between entry and stop.
    Rounds down to the symbol's qty step. Returns 0 if below minimum."""
    risk_amount = equity * (config.RISK_PCT / 100.0)
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        return 0.0
    qty = risk_amount / stop_distance
    # floor to qty_step (round(..., 9) guards against float artifacts)
    qty = math.floor(round(qty / qty_step, 9)) * qty_step
    qty = round(qty, 9)
    return qty if qty >= min_qty else 0.0


class DayTracker:
    """Tracks realized PnL and trade counts per UTC day / session.
    Persists to STATE_FILE so restarts don't reset limits."""

    def __init__(self, path=None):
        self.path = path or config.STATE_FILE
        self.state = {"date": self._today(), "realized_pnl": 0.0, "trades": {}}
        self._load()

    @staticmethod
    def _today():
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    saved = json.load(f)
                if saved.get("date") == self._today():
                    self.state = saved
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self.state, f)

    def _roll_day(self):
        if self.state["date"] != self._today():
            self.state = {"date": self._today(), "realized_pnl": 0.0, "trades": {}}
            self._save()

    def record_trade(self, session_name, side, pnl=0.0):
        self._roll_day()
        key = f"{session_name}:{side}"
        self.state["trades"][key] = self.state["trades"].get(key, 0) + 1
        self.state["realized_pnl"] += pnl
        self._save()

    def record_pnl(self, pnl):
        self._roll_day()
        self.state["realized_pnl"] += pnl
        self._save()

    def trades_taken(self, session_name, side):
        self._roll_day()
        return self.state["trades"].get(f"{session_name}:{side}", 0)

    def daily_loss_hit(self, equity):
        """True if today's realized losses exceed the daily limit."""
        self._roll_day()
        if equity <= 0:
            return True
        limit = equity * (config.DAILY_LOSS_LIMIT_PCT / 100.0)
        return self.state["realized_pnl"] <= -limit
