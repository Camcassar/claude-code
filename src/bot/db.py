"""SQLite persistence — compatible with the bot-dashboard trade reader."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT    NOT NULL,
                side        TEXT    NOT NULL,
                qty         REAL    NOT NULL,
                entry_price REAL    NOT NULL,
                exit_price  REAL,
                pnl_usd     REAL,
                opened_at   TEXT    NOT NULL,
                closed_at   TEXT,
                setup_type  TEXT DEFAULT 'spectral_ema'
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                equity_usd REAL    NOT NULL,
                ts         TEXT    NOT NULL
            )
        """)
        con.commit()


def open_trade(db: Path, symbol: str, side: str, qty: float, entry: float) -> int:
    with sqlite3.connect(db) as con:
        cur = con.execute(
            "INSERT INTO trades (symbol, side, qty, entry_price, opened_at) VALUES (?,?,?,?,?)",
            (symbol, side, qty, entry, _now()),
        )
        con.commit()
        return cur.lastrowid  # type: ignore[return-value]


def close_trade(db: Path, trade_id: int, exit_price: float, pnl: float) -> None:
    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE trades SET exit_price=?, pnl_usd=?, closed_at=? WHERE id=?",
            (exit_price, pnl, _now(), trade_id),
        )
        con.commit()


def snapshot_equity(db: Path, equity: float) -> None:
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO equity_snapshots (equity_usd, ts) VALUES (?,?)", (equity, _now()))
        con.commit()


def fetch_trade_stats(db: Path) -> dict:
    try:
        with sqlite3.connect(db) as con:
            row = con.execute("""
                SELECT
                    COUNT(*)                                             AS total,
                    SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END)       AS wins,
                    ROUND(SUM(pnl_usd), 2)                              AS total_pnl,
                    ROUND(MAX(pnl_usd), 2)                              AS best_trade,
                    ROUND(MIN(pnl_usd), 2)                              AS worst_trade
                FROM trades WHERE closed_at IS NOT NULL
            """).fetchone()
            total, wins, total_pnl, best, worst = row
            total = total or 0
            wins = wins or 0
            recent_rows = con.execute("""
                SELECT symbol, side AS setup_type, pnl_usd AS pnl, closed_at
                FROM trades WHERE closed_at IS NOT NULL
                ORDER BY id DESC LIMIT 10
            """).fetchall()
            recent = [
                {"symbol": r[0], "setup_type": r[1], "pnl": r[2], "closed_at": r[3]}
                for r in recent_rows
            ]
            return {
                "total": total,
                "wins": wins,
                "win_rate": round(wins / total * 100, 1) if total else 0,
                "total_pnl": total_pnl or 0,
                "best_trade": best or 0,
                "worst_trade": worst or 0,
                "recent": recent,
            }
    except Exception as e:
        return {"error": str(e)}
