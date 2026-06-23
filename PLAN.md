# Bot 8 — AVAX Spectral: Validate → Deploy Plan

**Goal:** decide which strategy variant (if any) has a real, fee-adjusted edge,
then deploy it to Railway and connect the live Bybit key — *without* putting real
money on an unvalidated strategy.

**One-line status:** the bot is fully built and Railway-ready. What's missing is
**evidence** that the strategy makes money on real data, plus a safe path to live.

---

## 0. Context & honest constraints

- **`trader.dev` was the source of the *theory*** (the spectral-centroid / EMA-octave
  idea), not a runtime dependency. The bot trades **Bybit directly** and never calls
  trader.dev. The `pk_…` key is unused by this codebase. → We set trader.dev aside.
- **This session's terminal is firewalled.** It cannot reach Bybit/Binance, so it
  cannot fetch real OHLCV, and `pandas`/`numpy` aren't installed. A *real* backtest
  must run somewhere with network access (see Decision A).
- **The current live filter is flagged broken.** Per `scripts/backtest.py`, the
  `centroid > 45` gate flags ~75–99% of *all* bars as "trending," giving no chop
  protection — which is where the strategy bleeds. The whole point of Phase 1 is to
  measure whether the fix (ADX gate / trailing stop) turns the edge positive.
- **No strategy is "guaranteed profitable."** Backtests overfit; past ≠ future. We
  treat a good backtest as *necessary but not sufficient*, and gate real money behind
  testnet validation (see Decision B).

---

## 1. The candidate strategies

These already exist in `scripts/backtest.py` — they ARE "the strategies" (the
trader.dev theory + the prior fix work), so there's nothing to go fetch:

| ID | Gate | Exit | Notes |
|----|------|------|-------|
| **V0** | spectral centroid > 45 | fixed TP/SL | the current live config — suspected broken |
| **V1** | ADX > 25 | fixed TP/SL | replaces broken filter with a real trend gate |
| **V2** | ADX > 25 | trailing stop | lets winners run |
| **V3** | none | fixed TP/SL | control — isolates how much the gate actually adds |

Indicator code (`_ema`, `_atr`, `_spectral_centroid`) is shared with the live bot, so
the backtest can't silently diverge from production.

---

## 2. Phase 1 — Backtest on REAL data  *(owner: depends on Decision A)*

Setup (wherever it runs):
```bash
pip install ccxt pandas numpy pyyaml
python scripts/backtest.py                       # Bybit AVAX/USDT 30m, 12 months
python scripts/backtest.py --months 6            # shorter window cross-check
python scripts/backtest.py --exchange binance    # different venue, same symbol
```
Capture: the printed comparison table (trades / win% / net% / profit factor / maxDD)
for each variant, plus the `logs/backtest_*.csv` equity curves.

**Robustness checks (so we don't ship an overfit number):**
- Run multiple windows (6mo, 12mo) and both venues (Bybit + Binance).
- Out-of-sample: confirm the winner holds on a period not used to pick it.
- Sanity: a "great" result with very few trades or one giant win = not real.

**Decision A — where does this run?** (this session can't reach exchanges)
- **A1 (recommended): allowlist `api.bybit.com` (+ `api.binance.com`) in this
  environment's egress settings**, then I install deps and run it *here* in your
  terminal, exactly as you asked.
- **A2:** you run the three commands on any machine with internet and paste the
  tables back; I do the analysis.

---

## 3. Phase 2 — Pick the strategy  *(owner: me)*

Selection criteria, in order:
1. **Profit factor > ~1.3** net of the fees/funding already modeled (taker 0.055%/side,
   funding ~0.01%/8h).
2. **Max drawdown** you can stomach at the configured sizing (see Risk register).
3. **Trade count** high enough to be statistically meaningful (rule of thumb ≥ ~50).
4. **Consistency** across windows/venues, not one lucky regime.

Output: a short written recommendation ("ship V_x, here's why; V_y rejected because…"),
and the exact `config/live.yaml` values for the winner. If *nothing* clears the bar,
the honest recommendation is **don't deploy live** — and I'll say so plainly.

---

## 4. Phase 3 — Paper / testnet validation  *(owner: you + me)*  *(gated by Decision B)*

`BybitConnector` already supports `testnet=True` (`set_sandbox_mode`). Before any real
funds we run the chosen config on **Bybit testnet** for long enough to confirm live
mechanics: orders fill, SL/TP attach, restart-reconciliation works, Telegram fires.

**Decision B — live-risk posture:**
- **B1 (recommended): validate → testnet → live.** No real money until Phase 1 looks
  good *and* testnet behaves.
- **B2: testnet first, then decide.**
- **B3: live as-is now.** I'll do it if you direct it, but on the record: the code says
  this strategy loses money in chop. I don't recommend it.

---

## 5. Phase 4 — Deploy to Railway  *(owner: you, I prepare everything)*

The repo is already Railway-ready (`railway.toml`: nixpacks, `startCommand = python main.py`).
Steps:
1. Create/point a Railway service at this repo + branch.
2. Add a **persistent volume** and set `BOT8_DB_PATH=/data/trades.db` so trade history
   survives redeploys (the code already honors this).
3. Set env vars (Decision: real vs testnet keys):
   - `BYBIT_API_KEY`, `BYBIT_API_SECRET`
   - `BOT8_LIVE_CONFIRMED=1`  (the bot refuses to trade without this)
   - optional: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
4. Deploy and watch logs for `bybit_connected` + the Telegram "running" message.

*I can't deploy or enter your secrets for you — that's your Railway account and your
key. I prepare configs/scripts/docs and walk you through each step.*

---

## 6. Phase 5 — Connect the Bybit key  *(owner: you)*

"Reconnecting the key" = setting `BYBIT_API_KEY/SECRET` in Railway and redeploying.
The code already guards the common failure modes:
- **Read-only key** → detected at startup (`_check_trade_permission`) with a clear
  Telegram alert. The key needs **Unified Trading → Trade (Read-Write)**.
- **Bad secret (whitespace/quotes)** → loud auth failure + a safe credential
  fingerprint in logs (never logs the secret itself).

Pre-flight: start on **testnet keys** (per Decision B) before swapping to real ones.

---

## 7. Phase 6 — Handover file  *(owner: me)* — *"the full file of this strategy"*

A single `HANDOVER.md` (or `docs/`) containing: the chosen strategy + exact params and
*why* it won, the backtest results table, how to run/deploy/operate, env-var reference,
the kill-switch + safety mechanisms, and known limitations. This is the artifact you
keep so the next session (or person) has full context.

---

## 8. Risk register (review before live)

- **Sizing is aggressive.** `equity_pct=0.40` × `leverage=3` × vol/anti-martingale
  multipliers (up to ~2×) → notional can reach ~3.6× equity. Worth dialing down for
  initial live runs.
- **Anti-martingale up-sizing** after wins amplifies both runs and reversals.
- **Single symbol / single venue** → no diversification; AVAX-specific regime risk.
- **Kill switch:** `logs/.kill_switch` file stops the bot; `BYBIT_*` removal + redeploy
  is the hard stop. Worth confirming you know how to pull it fast.

---

## 9. Definition of done

1. Real-data backtest table for V0–V3 ✅
2. A recommended variant + config (or a documented "don't ship") ✅
3. Testnet validation passed (if Decision B = B1/B2) ✅
4. Live on Railway with the connected key ✅
5. `HANDOVER.md` written ✅

---

## Open items I need from you
- **Decision A:** allowlist exchanges so I backtest *here* (A1), or you run + paste (A2)?
- **Decision B:** validation posture — B1 / B2 / B3?
- Real **or** testnet keys for the first deploy?
