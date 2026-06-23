"""SQLite persistence for the Bot Command Center.

Probe history and push check-ins live on disk so uptime survives restarts.
Path: $CC_DB_PATH (default dashboard/data/command_center.db). Samples older than
$CC_RETENTION_DAYS (default 7) are pruned on startup.

This is a tiny single-process localhost tool, and every DB call happens on the
event-loop thread, so one shared connection (guarded by a lock for writes) is
plenty. Swap for a pool if this ever grows up.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

LOG = logging.getLogger("command-center.store")

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None


def init(db_path: str, retention_days: float = 7) -> None:
    global _CONN
    p = Path(db_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    _CONN = sqlite3.connect(str(p), check_same_thread=False)
    _CONN.execute("PRAGMA journal_mode=WAL")
    _CONN.execute(
        "CREATE TABLE IF NOT EXISTS samples("
        "bot_id TEXT NOT NULL, t REAL NOT NULL, status TEXT NOT NULL, latency REAL)"
    )
    _CONN.execute("CREATE INDEX IF NOT EXISTS ix_samples_bot_t ON samples(bot_id, t)")
    _CONN.execute(
        "CREATE TABLE IF NOT EXISTS heartbeats("
        "bot_id TEXT PRIMARY KEY, last_seen REAL NOT NULL)"
    )
    _CONN.commit()
    if retention_days and retention_days > 0:
        prune(retention_days)


def record(bot_id: str, t: float, status: str, latency: float | None) -> None:
    with _LOCK:
        _CONN.execute(
            "INSERT INTO samples(bot_id, t, status, latency) VALUES(?,?,?,?)",
            (bot_id, t, status, latency),
        )
        _CONN.commit()


def recent(bot_id: str, limit: int) -> list[dict]:
    cur = _CONN.execute(
        "SELECT t, status, latency FROM samples WHERE bot_id=? ORDER BY t DESC LIMIT ?",
        (bot_id, limit),
    )
    rows = cur.fetchall()
    rows.reverse()  # oldest -> newest for the sparkline
    return [{"t": r[0], "status": r[1], "latency": r[2]} for r in rows]


def last_status(bot_id: str) -> str | None:
    cur = _CONN.execute(
        "SELECT status FROM samples WHERE bot_id=? ORDER BY t DESC LIMIT 1", (bot_id,)
    )
    row = cur.fetchone()
    return row[0] if row else None


def uptime(bot_id: str, since: float) -> tuple[int, int]:
    """Return (total_samples, up_samples) recorded at or after `since`."""
    cur = _CONN.execute(
        "SELECT COUNT(*), COALESCE(SUM(CASE WHEN status='up' THEN 1 ELSE 0 END), 0) "
        "FROM samples WHERE bot_id=? AND t>=?",
        (bot_id, since),
    )
    tot, up = cur.fetchone()
    return int(tot), int(up)


def save_heartbeat(bot_id: str, t: float) -> None:
    with _LOCK:
        _CONN.execute(
            "INSERT INTO heartbeats(bot_id, last_seen) VALUES(?,?) "
            "ON CONFLICT(bot_id) DO UPDATE SET last_seen=excluded.last_seen",
            (bot_id, t),
        )
        _CONN.commit()


def load_heartbeats() -> dict[str, float]:
    cur = _CONN.execute("SELECT bot_id, last_seen FROM heartbeats")
    return {r[0]: r[1] for r in cur.fetchall()}


def prune(days: float) -> int:
    cutoff = time.time() - days * 86400
    with _LOCK:
        cur = _CONN.execute("DELETE FROM samples WHERE t < ?", (cutoff,))
        _CONN.commit()
    if cur.rowcount:
        LOG.info("pruned %d samples older than %sd", cur.rowcount, days)
    return cur.rowcount
