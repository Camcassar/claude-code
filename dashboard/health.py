"""Health probes for the Bot Command Center.

Each bot in ``bots.yaml`` declares a ``probe``. We support a handful of probe
types so a dashboard running on your Mac can cover the realistic cases:

  http            GET a URL; 2xx/3xx = up. Railway web services or any bot that
                  exposes an HTTP endpoint.
  tcp             open host:port. Local bots that listen on a port but have no
                  health route.
  heartbeat_file  check a file's mtime; fresh = up, old = stale. Local worker
                  bots that periodically touch a file.
  push            the bot POSTs /api/heartbeat/<id> to check in; we track the
                  last time we heard from it. Remote workers (e.g. Railway) that
                  can reach this dashboard but expose no inbound port.
  none            no automated probe; always reported as 'unknown'.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

LOG = logging.getLogger("command-center.health")

UP = "up"
DOWN = "down"
STALE = "stale"
UNKNOWN = "unknown"

DEFAULT_TIMEOUT = 5.0


@dataclass
class ProbeResult:
    status: str
    detail: str = ""
    latency_ms: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def _check_http(probe: dict) -> ProbeResult:
    url = probe.get("url")
    if not url:
        return ProbeResult(UNKNOWN, "http probe missing 'url'")
    timeout = float(probe.get("timeout", DEFAULT_TIMEOUT))
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
        latency = (time.perf_counter() - start) * 1000
        label = f"HTTP {resp.status_code}"
        return ProbeResult(UP if resp.status_code < 400 else DOWN, label, latency)
    except Exception as e:  # noqa: BLE001 — any connection error means "down"
        return ProbeResult(DOWN, f"{type(e).__name__}: {e}".strip()[:140])


async def _check_tcp(probe: dict) -> ProbeResult:
    host = probe.get("host", "127.0.0.1")
    port = probe.get("port")
    if not port:
        return ProbeResult(UNKNOWN, "tcp probe missing 'port'")
    timeout = float(probe.get("timeout", DEFAULT_TIMEOUT))
    start = time.perf_counter()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port)), timeout=timeout
        )
        latency = (time.perf_counter() - start) * 1000
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001 — close errors don't change liveness
            pass
        return ProbeResult(UP, f"{host}:{port} open", latency)
    except Exception as e:  # noqa: BLE001
        return ProbeResult(DOWN, f"{host}:{port} — {type(e).__name__}")


def _check_heartbeat_file(probe: dict) -> ProbeResult:
    raw = probe.get("path")
    if not raw:
        return ProbeResult(UNKNOWN, "heartbeat_file probe missing 'path'")
    stale_after = float(probe.get("stale_after", 3600))
    p = Path(raw).expanduser()
    if not p.exists():
        return ProbeResult(DOWN, f"no file at {p}")
    age = time.time() - p.stat().st_mtime
    detail = f"touched {_ago(age)} ago"
    return ProbeResult(UP if age <= stale_after else STALE, detail)


def _check_push(probe: dict, last_seen: float | None) -> ProbeResult:
    stale_after = float(probe.get("stale_after", 3600))
    if last_seen is None:
        return ProbeResult(UNKNOWN, "no check-in received yet")
    age = time.time() - last_seen
    detail = f"checked in {_ago(age)} ago"
    return ProbeResult(UP if age <= stale_after else STALE, detail)


def _ago(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


async def probe_bot(bot: dict, heartbeats: dict[str, float]) -> dict[str, Any]:
    probe = bot.get("probe") or {"type": "none"}
    ptype = probe.get("type", "none")
    try:
        if ptype == "http":
            result = await _check_http(probe)
        elif ptype == "tcp":
            result = await _check_tcp(probe)
        elif ptype == "heartbeat_file":
            result = _check_heartbeat_file(probe)
        elif ptype == "push":
            result = _check_push(probe, heartbeats.get(bot.get("id", "")))
        elif ptype == "none":
            result = ProbeResult(UNKNOWN, "no probe configured")
        else:
            result = ProbeResult(UNKNOWN, f"unknown probe type '{ptype}'")
    except Exception as e:  # noqa: BLE001 — a bad probe must never 500 the page
        LOG.exception("probe failed for %s", bot.get("id"))
        result = ProbeResult(UNKNOWN, f"probe error: {type(e).__name__}")

    return {
        "id": bot.get("id"),
        "name": bot.get("name", bot.get("id")),
        "kind": bot.get("kind", "other"),
        "host": bot.get("host", "unknown"),
        "repo": bot.get("repo", ""),
        "notes": (bot.get("notes") or "").strip(),
        "tags": bot.get("tags", []),
        "probe_type": ptype,
        **result.as_dict(),
    }


async def probe_all(bots: list[dict], heartbeats: dict[str, float]) -> list[dict]:
    if not bots:
        return []
    return list(await asyncio.gather(*(probe_bot(b, heartbeats) for b in bots)))
