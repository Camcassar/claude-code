# Day Trader Bot — ORB + VWAP (Bybit perps)

Automated day-trading bot: opening range breakout with VWAP bias and volume
confirmation on SOLUSDT 5m candles. Two sessions per day (UTC open, US open),
risk-based sizing, server-side SL/TP, breakeven at +1R, flat by session close.

## Strategy in one paragraph

At each session open (00:00 and 13:30 UTC) the first 30 minutes set the
opening range. A 5m candle *closing* outside the range — on ≥2× average
volume, on the right side of session VWAP, with the range itself between
0.5–3× ATR — triggers a market entry. Stop at the opposite side of the range,
take profit at 3R, stop moves to entry at +1R, anything still open is closed
at session end. 1% equity risk per trade, max one long + one short per
session, hard stop for the day at −3% realized.

## Setup (macOS)

```bash
cd day-trader-bot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your testnet API keys
```

Testnet keys: https://testnet.bybit.com → API Management. Get testnet USDT
from the faucet on the same site.

## Run

```bash
python bot.py            # live loop (testnet while TESTNET=true)
python test_strategy.py  # 28 unit tests
python backtest.py --days 60                    # backtest on fresh history
python backtest.py --csv sol_5m_history.csv     # replay the bundled 90 days
```

## Going live on a Bybit subaccount

1. Create subaccount, fund it, generate API keys under it (contract trade perms)
2. In `.env`: paste the new keys and set `TESTNET=false`
3. No code changes — sizing and loss limits operate on subaccount equity only

## Deploy to Railway

The Dockerfile is ready. Create a new service in your existing project,
point it at this folder (or a GitHub repo of it), and set the env vars
`BYBIT_API_KEY`, `BYBIT_API_SECRET`, `TESTNET` in the service settings.
Note: a 24/7 bot consumes trial credit continuously — you'll likely need the
Hobby plan to run it alongside the AVAX bot.

## Backtest results (context, not a promise)

90 days of SOLUSDT (Mar–Jun 2026), $10k start, fees included:
+14.25%, 116 trades, 24% win rate, profit factor 1.20, positive in both
halves of a walk-forward split. Low win rate is by design (3R targets);
expect long losing streaks. Past performance guarantees nothing — re-run
`backtest.py` on fresh data before going live, and treat the first weeks
on testnet as the real test.

## Files

| File | Purpose |
|---|---|
| `bot.py` | Main loop: poll, signal, execute, manage position |
| `strategy.py` | Pure ORB+VWAP signal logic (no API calls) |
| `indicators.py` | VWAP, EMA, ATR, volume average |
| `risk.py` | Position sizing, daily loss tracking (persists to JSON) |
| `exchange.py` | Bybit v5 wrapper (pybit) — all API calls live here |
| `config.py` | Every tunable + env vars |
| `backtest.py` | Historical replay of the exact strategy logic |
| `test_strategy.py` | Unit tests on synthetic candles |
