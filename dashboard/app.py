"""Bot Command Center — a one-page status board for all your bots.

Run it on your Mac:

    cd dashboard
    pip install -r requirements.txt
    python app.py                      # -> http://localhost:8000

Edit ``bots.yaml`` to register bots; the page re-reads it on every refresh, so
you never need to restart to add a bot.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from health import probe_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("command-center")

HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "bots.yaml"
STATIC = HERE / "static"

REFRESH_SECONDS = 15
HISTORY_MAX = 120  # ~30 min of samples at the default 15s cadence

app = FastAPI(title="Bot Command Center")

# Rolling per-bot history of probe results, plus the timestamp each bot entered
# its current status. In-memory only — resets on restart, which is fine for a
# local dashboard. Push check-ins land in _heartbeats.
_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=HISTORY_MAX))
_state: dict[str, dict] = {}
_heartbeats: dict[str, float] = {}


def _load_registry() -> list[dict]:
    if not REGISTRY.exists():
        LOG.warning("registry not found at %s", REGISTRY)
        return []
    data = yaml.safe_load(REGISTRY.read_text()) or {}
    bots = data.get("bots", []) or []
    # Keep only entries with an id — history is keyed by it.
    return [b for b in bots if isinstance(b, dict) and b.get("id")]


def _record(bot_id: str, status: str, latency: float | None, now: float) -> float:
    """Append a sample and return the timestamp this status started."""
    _history[bot_id].append({"t": now, "status": status, "latency": latency})
    st = _state.get(bot_id)
    if st is None or st["status"] != status:
        st = {"status": status, "since": now}
        _state[bot_id] = st
    return st["since"]


@app.get("/api/status")
async def api_status() -> JSONResponse:
    now = time.time()
    bots = _load_registry()
    results = await probe_all(bots, _heartbeats)

    summary = {"up": 0, "down": 0, "stale": 0, "unknown": 0, "total": len(results)}
    enriched: list[dict] = []
    latencies: list[float] = []
    total_samples = up_samples = 0

    for r in results:
        bid = r["id"]
        since = _record(bid, r["status"], r.get("latency_ms"), now)
        hist = list(_history[bid])
        statuses = [h["status"] for h in hist]
        up_ct = sum(1 for s in statuses if s == "up")
        total_samples += len(statuses)
        up_samples += up_ct

        summary[r["status"]] = summary.get(r["status"], 0) + 1
        if r["status"] == "up" and r.get("latency_ms") is not None:
            latencies.append(r["latency_ms"])

        enriched.append({
            **r,
            "history": statuses,
            "latency_history": [h["latency"] for h in hist],
            "uptime_pct": round(100 * up_ct / len(statuses), 1) if statuses else 0.0,
            "checks": len(statuses),
            "since": since,
        })

    summary["overall_uptime"] = (
        round(100 * up_samples / total_samples, 1) if total_samples else 0.0
    )
    summary["avg_latency_ms"] = (
        round(sum(latencies) / len(latencies), 1) if latencies else None
    )
    summary["checks"] = total_samples
    summary["window"] = HISTORY_MAX

    return JSONResponse({
        "generated_at": now,
        "refresh_hint": REFRESH_SECONDS,
        "summary": summary,
        "bots": enriched,
    })


@app.post("/api/heartbeat/{bot_id}")
async def api_heartbeat(bot_id: str) -> JSONResponse:
    """Push-style bots POST here each loop to report they're alive."""
    _heartbeats[bot_id] = time.time()
    return JSONResponse({"ok": True, "bot_id": bot_id, "at": _heartbeats[bot_id]})


@app.get("/api/bots")
async def api_bots() -> JSONResponse:
    """Raw registry, for debugging what the dashboard loaded."""
    return JSONResponse({"bots": _load_registry()})


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


if __name__ == "__main__":
    import uvicorn

    LOG.info("Bot Command Center → http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)
