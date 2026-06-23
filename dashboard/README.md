# 🛰️ Bot Command Center

One page, on `localhost:8000`, that shows **every bot you own** and whether it's
alive. `bots.yaml` is the single source of truth for "where are all my bots" —
edit it and the dashboard updates on the next refresh. Uptime history is
persisted to SQLite, so it survives restarts.

> **Orientation for the whole setup lives in [`/CLAUDE.md`](../CLAUDE.md).**
> Seeded 2026-06 from the owner's GitHub. Of everything there, **Bot 8 is the
> only live trading bot**; CamFlow and yahoo-finance-tool turned out to be local
> Mac apps (voice dictation / a finance GUI), not bots. Bots that live only on
> the Mac or only on Railway aren't visible from a cloud session — add them
> yourself with a `probe` block (examples in `bots.yaml`).

## Quick start

```bash
cd dashboard
pip install -r requirements.txt
cp .env.example .env          # optional — tweak port, retention, Telegram
python app.py                 # → http://localhost:8000
```

(Or `uvicorn app:app --port 8000 --reload` while editing.)

## What you get

- **Fleet health ring** — overall 24h uptime % across all bots, animated.
- **Live tiles** — online / down / stale / unknown counts + average latency.
- **Per-bot cards** — status dot, latency, time-in-state, notes, tags, and a
  **sparkline** of the last ~120 checks (bar height scaled by latency).
- **24h uptime %** per bot, computed from the persisted history.
- **Search** (name / tag / host / kind) and **group by** host, kind, or status.
- **Auto-refresh** every 15s with a countdown + pause, and a top progress bar.
- **Toasts** in the browser + optional **Telegram alerts** when a bot drops or
  recovers.

## The registry (`bots.yaml`)

```yaml
bots:
  - id: bot8                      # unique; health history is keyed by this
    name: "Bot 8 — AVAX Spectral"
    kind: trading                 # free-form: trading | tool | desktop-app | …
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
| `process`        | `pgrep -f "match"` finds a PID                  | local Mac apps/bots with no server (e.g. CamFlow) |
| `heartbeat_file` | checks a file's mtime; fresh = up, old = stale | local workers that touch a file each loop |
| `push`           | bot POSTs `/api/heartbeat/<id>`; tracks last check-in | remote workers (Railway) that can reach this dashboard |
| `none`           | always `unknown`                               | bots you just want listed and track manually |

```yaml
probe: { type: http, url: "https://bot.up.railway.app/healthz", timeout: 5 }
probe: { type: tcp, host: 127.0.0.1, port: 8001 }
probe: { type: process, match: "bot6/main.py" }
probe: { type: heartbeat_file, path: ~/bots/x/heartbeat, stale_after: 300 }
probe: { type: push, stale_after: 2400 }
```

## Wiring up the bots a cloud session couldn't reach

**Local bots (Mac).** `tcp` if they bind a port; `process` to match a running
`python …` by command line; or `heartbeat_file` for a worker that `touch`es a
file each loop.

**Railway web services.** Add a health route and use `http`.

**Railway workers with no port (like Bot 8).** They can't be pinged, so have the
bot *check in*. Drop this into its loop and set `type: push`:

```python
import httpx  # Bot 8 already has requests too — either works
DASHBOARD = "http://<your-mac-or-tunnel>:8000"   # must be reachable from the bot
try:
    httpx.post(f"{DASHBOARD}/api/heartbeat/bot8", timeout=5)
except Exception:
    pass  # never let telemetry break a trade loop
```

> A `localhost` dashboard isn't reachable from Railway. For `push` from a remote
> bot, expose this app on a URL the bot can hit (a `cloudflared`/`ngrok` tunnel,
> or deploy the dashboard). Purely-local `http`/`tcp`/`process`/`heartbeat_file`
> probes work as-is.

## Configuration (`.env`, all optional)

| Var | Default | Purpose |
|-----|---------|---------|
| `CC_DB_PATH` | `data/command_center.db` | SQLite history file (relative paths resolve under `dashboard/`) |
| `CC_RETENTION_DAYS` | `7` | prune samples older than this on startup |
| `CC_HOST` / `CC_PORT` | `127.0.0.1` / `8000` | bind address |
| `CC_REFRESH_SECONDS` | `15` | UI refresh cadence |
| `CC_TELEGRAM_BOT_TOKEN` / `CC_TELEGRAM_CHAT_ID` | — | set both to enable Telegram alerts |

## API

| Route                       | Purpose |
|-----------------------------|---------|
| `GET /`                     | the dashboard |
| `GET /api/status`           | JSON: summary + per-bot status, history, 24h uptime, latency |
| `GET /api/bots`             | the raw registry the server loaded |
| `POST /api/heartbeat/{id}`  | check-in endpoint for `push` bots |

## Files

```
dashboard/
  app.py            FastAPI app + /api/status aggregation
  health.py         the probes (http/tcp/process/heartbeat_file/push/none)
  store.py          SQLite persistence (history, uptime, heartbeats)
  notify.py         optional Telegram alerts on status changes
  bots.yaml         the registry — edit this
  static/index.html the dashboard UI (single self-contained file)
  data/             SQLite db lives here (gitignored)
```

## Notes & limits

- History persists to SQLite; `unknown` just means "no probe wired yet".
- Not built yet: a Railway-API probe (would need a `RAILWAY_TOKEN`). For now,
  Railway bots use `http` (web services) or `push` (workers).
- This lives inside the `claude-code` (Bot 8) repo because that's where it was
  generated. It's fully self-contained — move it to its own repo anytime.
