# CLAUDE.md — start here

This repo is **two things**:

1. **Bot 8 — AVAX Spectral**: a live crypto trading bot (root: `main.py`,
   `src/bot/`, `config/live.yaml`). Trades AVAX/USDT perps on Bybit, 30m bars,
   deploys to Railway.
2. **Bot Command Center**: a dashboard in **`dashboard/`** giving one
   `localhost:8000` view of *all* the owner's bots. Built 2026-06 to consolidate
   bots that were scattered across Railway and macOS.

**New session? Read this file → `dashboard/README.md` → `dashboard/bots.yaml`.**

## The bot fleet (what we know)

| Bot | What it actually is | Where | Tracked via |
|-----|---------------------|-------|-------------|
| **Bot 8 — AVAX Spectral** | Live Bybit AVAX/USDT perp trader (this repo) | Railway | `push` heartbeat (needs wiring) |
| **CamFlow** | macOS voice-dictation app (Whisper) — **not a trading bot** | Mac (public GitHub) | `none` / `process` |
| **Yahoo Finance Tool** | Tkinter GUI for Yahoo Finance charts — **not a bot, no trades** | Mac (public GitHub) | `none` (run on demand) |
| CC-claude | empty "Claude test" repo | GitHub | — |
| software_testing / Software_tech / Workshop2 | 2024 coursework | GitHub | — |

**Key finding:** across the owner's GitHub, **Bot 8 is the only live trading
bot.** CamFlow and yahoo-finance-tool are local Mac utilities. The owner has said
there are more bots "scattered on the Mac and Railway" — "Bot 8" implies Bots 1–7
existed. Those aren't on GitHub, so they're old/overwritten or **Mac/Railway-only
and must be added to `dashboard/bots.yaml` by someone who can see them.**

## What a cloud session CANNOT see (don't waste time)

A Claude Code **web/cloud** session runs in an ephemeral container with only
**this repo** cloned. From there you cannot reach:

- the owner's **Mac filesystem** (Mac-only bots),
- the owner's **Railway** account/dashboard,
- other GitHub repos' files via the **GitHub MCP** (it's scoped to `claude-code`).
  *Public* repos are still readable over the web — that's how CamFlow and
  yahoo-finance-tool were triaged (`raw.githubusercontent.com`, the GitHub API,
  or the repo's HTML page via WebFetch).

To inspect private repos or the Mac/Railway hosts, run a session **on the Mac**,
or scope a session to the target repo.

## Open tasks / next steps

1. **Register the hidden bots.** On the Mac (or a locally-running session), work
   through the **discovery checklist at the bottom of `dashboard/bots.yaml`**
   (`railway list`, `pgrep -fal python | grep -i bot`, `ls ~/Library/LaunchAgents`)
   and add real entries.
2. **Wire Bot 8's heartbeat** so its card goes green — add a
   `POST /api/heartbeat/bot8` to its 30m loop (snippet in `dashboard/README.md`).
3. **Optional:** a Railway-API probe (needs a `RAILWAY_TOKEN`) — not built yet.

## Running the dashboard

```bash
cd dashboard
pip install -r requirements.txt
python app.py            # → http://localhost:8000
```

- Registry: `dashboard/bots.yaml` (hot-reloaded each refresh).
- Persistence: SQLite at `dashboard/data/command_center.db` (uptime survives
  restarts; `CC_DB_PATH` to change). Config: `dashboard/.env.example`.
- Probes: `http` / `tcp` / `process` / `heartbeat_file` / `push` / `none`
  (`dashboard/health.py`).
- Optional Telegram alerts on status changes (`dashboard/notify.py`).
- API: `GET /api/status`, `GET /api/bots`, `POST /api/heartbeat/{id}`.

## Bot 8 (the trading bot) quick facts

- Run: `python main.py config/live.yaml` (or `make run`). Needs `BYBIT_API_KEY`,
  `BYBIT_API_SECRET`, and `BOT8_LIVE_CONFIRMED=1` in `.env`.
- Strategy `AvaxSpectralStrategy` (`src/bot/strategy.py`): EMA 20/60 +
  spectral-centroid trend filter, SL 2% / TP 3.5%, 12h time-exit, long+short.
- Persists trades to SQLite, sends Telegram alerts, deploys on Railway
  (`railway.toml`, restart=always). Single-instance flock + a live-trade guard.

## Conventions

- Active dev branch for the consolidation work: `claude/compassionate-ramanujan-g6agto`.
- Never commit secrets. `.env`, `*.db`, and `logs/` are gitignored.
- The dashboard is self-contained under `dashboard/` and can be lifted into its
  own repo later if you'd rather not keep it inside the Bot 8 repo.
