# 🛰️ Bot Command Center

One page, on `localhost:8000`, that shows **every bot you own** and whether it's
alive. The registry (`bots.yaml`) is the single source of truth for "where are
all my bots" — edit it and the dashboard updates on the next refresh.

> Seeded on 2026-06-23 from what was discoverable on your GitHub account
> (`Bot 8 / claude-code`, plus `CamFlow` and `yahoo-finance-tool`). Bots that
> live only on your Mac or only on Railway aren't visible from a cloud session —
> add them yourself with a `probe` block (examples below).

## Quick start

```bash
cd dashboard
pip install -r requirements.txt
python app.py          # → http://localhost:8000
```

(Or `uvicorn app:app --port 8000 --reload` while you're editing.)

## What you get

- **Fleet health ring** — overall uptime % across all bots, animated.
- **Live tiles** — online / down / stale / unknown counts + average latency.
- **Per-bot cards** with a status dot, latency, time-in-state, notes, tags, and
  a **sparkline** of the last ~34 health checks (height scaled by latency).
- **Search** (name / tag / host / kind) and **group by** host, kind, or status.
- **Auto-refresh** every 15s with a countdown + pause button, and a top progress
  bar on each sweep.
- **Toasts** when a bot transitions to down/stale or recovers.
- Rolling uptime % and check counts per bot (kept in memory, ~30 min window).

## The registry (`bots.yaml`)

```yaml
bots:
  - id: bot8                      # unique; health history is keyed by this
    name: "Bot 8 — AVAX Spectral"
    kind: trading                 # free-form: trading | tool | scaffold | …
    host: railway                 # free-form: railway | mac | localhost | github-only
    repo: https://github.com/...  # optional link shown on the card
    notes: "..."                  # optional blurb
    tags: [crypto, live]          # optional chips
    probe:
      type: push
      stale_after: 2400
```

### Probe types

| `type`           | What it does                                  | Use for |
|------------------|-----------------------------------------------|---------|
| `http`           | `GET url`; 2xx/3xx = up                        | Railway **web** services, any bot with an HTTP route |
| `tcp`            | opens `host:port`                              | local bots listening on a port, no health route |
| `heartbeat_file` | checks a file's mtime; fresh = up, old = stale | local workers that touch a file each loop |
| `push`           | bot POSTs `/api/heartbeat/<id>`; tracks last check-in | remote workers (Railway) that can reach this dashboard |
| `none`           | always `unknown`                               | bots you just want listed and track manually |

```yaml
# http
probe: { type: http, url: "https://bot.up.railway.app/healthz", timeout: 5 }
# tcp
probe: { type: tcp, host: 127.0.0.1, port: 8001 }
# heartbeat file
probe: { type: heartbeat_file, path: ~/bots/x/heartbeat, stale_after: 300 }
# push
probe: { type: push, stale_after: 2400 }
```

## Wiring up the bots I couldn't reach

**Local bots (Mac).** Easiest is `tcp` if they bind a port, or have them
`touch` a heartbeat file each loop and point a `heartbeat_file` probe at it.

**Railway web services.** Add a health route and use `http`.

**Railway workers with no port (like Bot 8).** They can't be pinged, so have the
bot *check in*. Drop this into its main loop and set `type: push`:

```python
import httpx  # already have requests in Bot 8 — either works
DASHBOARD = "http://<your-mac-or-tunnel>:8000"   # must be reachable from the bot
try:
    httpx.post(f"{DASHBOARD}/api/heartbeat/bot8", timeout=5)
except Exception:
    pass  # never let telemetry break a trade loop
```

> A `localhost` dashboard isn't reachable from Railway. To use `push` from a
> remote bot, expose this app on a URL the bot can hit (a tunnel like
> `cloudflared`/`ngrok`, or deploy the dashboard itself). For a purely local
> setup, `http`/`tcp`/`heartbeat_file` against things on your Mac work as-is.

## API

| Route                       | Purpose |
|-----------------------------|---------|
| `GET /`                     | the dashboard |
| `GET /api/status`           | JSON: summary + per-bot status, history, uptime |
| `GET /api/bots`             | the raw registry the server loaded |
| `POST /api/heartbeat/{id}`  | check-in endpoint for `push` bots |

## Notes & limits

- History is **in-memory** — it resets when you restart `app.py`. Good enough
  for a desk dashboard; swap in SQLite later if you want persistence.
- `unknown` just means "no probe wired yet", not "broken".
- This currently lives inside the `claude-code` (Bot 8) repo because that's where
  it was generated. It's fully self-contained — move it to its own repo anytime.
