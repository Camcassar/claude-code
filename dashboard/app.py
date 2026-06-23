"""Bot Command Center — a one-page status board for all your bots.

Run it on your Mac:

    cd dashboard
    pip install -r requirements.txt
    python app.py                      # -> http://localhost:8000

Edit bots.yaml to register bots; the page re-reads it on every refresh, so you
never restart to add a bot. History is persisted to SQLite (store.py) so uptime
survives restarts, and optional Telegram alerts (notify.py) fire on status
changes. Full orientation for this whole setup lives in /CLAUDE.md at the root.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import notify
import store
from health import probe_all

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("command-center")

REGISTRY = HERE / "bots.yaml"
STATIC = HERE / "static"

REFRESH_SECONDS = int(os.getenv("CC_REFRESH_SECONDS", "15"))
HISTORY_MAX = 120                       # samples shown in each card's sparkline
UPTIME_WINDOW = 24 * 3600               # uptime % computed over this trailing window

DB_PATH = os.getenv("CC_DB_PATH") or str(HERE / "data" / "command_center.db")
if not Path(DB_PATH).is_absolute():
    DB_PATH = str(HERE / DB_PATH)
RETENTION_DAYS = float(os.getenv("CC_RETENTION_DAYS", "7"))

store.init(DB_PATH, RETENTION_DAYS)
LOG.info("persistence: %s (retention %sd)", DB_PATH, RETENTION_DAYS)
LOG.info("telegram alerts: %s", "on" if notify.enabled() else "off")

app = FastAPI(title="Bot Command Center")


def _load_registry() -> list[dict]:
    if not REGISTRY.exists():
        LOG.warning("registry not found at %s", REGISTRY)
        return []
    data = yaml.safe_load(REGISTRY.read_text()) or {}
    bots = data.get("bots", []) or []
    return [b for b in bots if isinstance(b, dict) and b.get("id")]


def _streak_start(samples: list[dict], status: str, now: float) -> float:
    """Timestamp the current status streak began (clamped to the loaded window)."""
    since = now
    for s in reversed(samples):
        if s["status"] == status:
            since = s["t"]
        else:
            break
    return since


@app.get("/api/status")
async def api_status() -> JSONResponse:
    now = time.time()
    bots = _load_registry()
    heartbeats = store.load_heartbeats()
    results = await probe_all(bots, heartbeats)

    summary = {"up": 0, "down": 0, "stale": 0, "unknown": 0, "total": len(results)}
    enriched: list[dict] = []
    latencies: list[float] = []
    up_total = sample_total = 0
    transitions: list[tuple[dict, str]] = []

    for r in results:
        bid = r["id"]
        prev = store.last_status(bid)
        store.record(bid, now, r["status"], r.get("latency_ms"))
        if prev and prev != r["status"]:
            transitions.append((r, prev))

        hist = store.recent(bid, HISTORY_MAX)
        tot, up = store.uptime(bid, now - UPTIME_WINDOW)
        up_total += up
        sample_total += tot

        summary[r["status"]] = summary.get(r["status"], 0) + 1
        if r["status"] == "up" and r.get("latency_ms") is not None:
            latencies.append(r["latency_ms"])

        enriched.append({
            **r,
            "history": [h["status"] for h in hist],
            "latency_history": [h["latency"] for h in hist],
            "uptime_pct": round(100 * up / tot, 1) if tot else 0.0,
            "checks": tot,
            "since": _streak_start(hist, r["status"], now),
        })

    summary["overall_uptime"] = round(100 * up_total / sample_total, 1) if sample_total else 0.0
    summary["avg_latency_ms"] = round(sum(latencies) / len(latencies), 1) if latencies else None
    summary["checks"] = sample_total
    summary["window"] = HISTORY_MAX

    for r, prev in transitions:
        LOG.info("transition %s: %s -> %s", r["id"], prev, r["status"])
        asyncio.create_task(notify.transition(r, prev))

    return JSONResponse({
        "generated_at": now,
        "refresh_hint": REFRESH_SECONDS,
        "summary": summary,
        "bots": enriched,
    })


@app.post("/api/heartbeat/{bot_id}")
async def api_heartbeat(bot_id: str) -> JSONResponse:
    """Push-style bots POST here each loop to report they're alive."""
    now = time.time()
    store.save_heartbeat(bot_id, now)
    return JSONResponse({"ok": True, "bot_id": bot_id, "at": now})


@app.get("/api/bots")
async def api_bots() -> JSONResponse:
    """Raw registry the server loaded — handy for debugging bots.yaml."""
    return JSONResponse({"bots": _load_registry()})


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("CC_HOST", "127.0.0.1")
    port = int(os.getenv("CC_PORT", "8000"))
    LOG.info("Bot Command Center → http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port)
