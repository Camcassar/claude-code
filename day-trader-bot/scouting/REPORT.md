# Velocity-Z v2 — reverse-engineering & improvement report

## What the original (Hilbert F36 ETH 1h) was doing

From its 136 forensically-verified trades: a **momentum-explosion system**.
Enter when the z-score of smoothed price velocity exceeds ±2 ("minVelZ=2.0"),
hold with fixed brackets TP +5.5% / SL -2.5% (2.2:1), no other exit, one
position at a time, long and short. Avg hold 34 hours.

Our best-fit replica (EMA-8 smoother, 3-bar velocity, z-window 144,
threshold 2.5) reproduces **74% of their entries** same-side within 2 bars.
Their true smoother is likely a Hilbert-transform filter (less lag) — the
remaining 26% gap. Replica PF is lower than theirs (1.2 vs 2.3) because our
approximation also fires on weaker spikes theirs skipped.

## What went right

- The asymmetric bracket is the engine: 49% win rate × 2.2:1 payoff.
- Edge present in 2024 (PF 2.38), 2025 (2.10), 2026 (1.86) — real, multi-regime.
- Both longs (PF 2.00) and shorts (2.56) worked.

## What went wrong

1. **Apr–May 2026 streak: -7 straight losses.** Mostly longs bought into
   momentum spikes against a falling 400h trend. Counter-trend entries win
   only 44% at long horizons (vs 52% aligned).
2. **Volatility extremes hurt.** Mid-vol entries (ATR24 0.8–1.1%/bar) earn
   ~2x the expectancy of very quiet or panicked markets.
3. **Reckless sizing.** Up to 4.5x-equity notional → single trades lost >11%
   of equity; true max DD 11.7% (claimed 7.9%), pure luck it wasn't worse.
4. **Claimed metrics slightly flattering** (DD understated; Sharpe inflated
   by sampling).

## What we changed (each tested train 2024-25 / out-of-sample 2026)

| variant | train net/DD/PF | OOS 2026 net/DD/PF | verdict |
|---|---|---|---|
| baseline replica | +125% / 21.6% / 1.22 | +29% / 21.2% / 1.39 | works |
| ATR-scaled brackets | +195% / 24% / 1.34 | +7% / 20% / 1.08 | overfit, rejected |
| mid-vol filter | +212% / 19% / 1.52 | +14% / 21% / 1.24 | overfit, rejected |
| **400h trend filter** | +116% / 17% / 1.33 | **+30% / 19% / 1.59** | **kept** |
| filter stacks (A+B+C) | +226% / 12% / 2.01 | **-6% / 0.84** | textbook overfit, rejected |
| **trend + 2% risk sizing** | +89% / 13.7% / 1.34 | **+24% / 15.5% / 1.61** | **FINAL** |

Threshold sweep confirmed 2.5 (higher = better in-sample, worse OOS).

## Final spec (velocity_bot.py)

ETHUSDT 1h · EMA-8 velocity z-score (window 144) · enter |z| ≥ 2.5 only with
the 400h trend · TP 5.5% / SL 2.5% server-side brackets · 2% equity risk per
trade · circuit breaker: 4 straight losses → 3-day pause (risk rule, not
fitted) · ~10-15 trades/month.

## Honest expectations

OOS result (+23.5% in 5.3 months, max DD 15.5%, PF 1.61) is one period on one
coin. The original's edge is decaying year over year, and our replica is an
approximation, not their code. Run on testnet ≥ 2-4 weeks; size small at
first. If trader.dev is upgraded later, diff their Pine source against this
and reconcile.

---

# Velocity-Z v3 — re-reverse-engineering update (2026-06-12)

## The big one: v2 was never trading the strategy we backtested

`velocity_bot.py` v2 defined `INTERVAL = "60"` but **never passed it to the
exchange wrapper** — `Bybit.get_candles()` fell back to `config.TIMEFRAME`,
which is the ORB bot's **"5" (5-minute candles)**. So the live v2 bot ran the
1h-fitted signal on 5m bars:

- z-score window: 144 x 5m = **12 hours** of velocity context instead of 6 days
- "400h trend filter": really a **33-hour** filter
- TP 5.5% / SL 2.5% brackets sitting on 5m noise spikes
- explains the absurd signal density in velocity_bot.log

None of the v2 backtest evidence (OOS +23.5%, PF 1.61) applied to what the
bot would actually have traded. **v3 passes `interval` explicitly** and the
exchange wrapper now takes it as a constructor argument.

## New findings from the 136-trade forensic list (scouting/analyze_losses.py)

1. **The original flips on opposite signal.** Trade #35 (SHORT) exits at
   exactly trade #36's entry time AND price (Sep 19 13:00, 2399.55) with a
   -1.67% loss — not a bracket level. The strategy stops-and-reverses when
   an opposite signal fires mid-position (observed once in 2.4y). v3
   implements this (`ALLOW_FLIP`, default on, gated by the same filters).
2. **SL slippage is real.** Trade #52 lost **-4.32%** against a -2.58%
   bracket (Dec 2024 crash bar gapped through the stop). Fixed-% sizing that
   assumes the SL price is guaranteed understates tail risk by ~70% on such
   bars. v3 sizes against `SL_PCT * SLIP_BUFFER (1.2)`.
3. **Same-side re-entry after a loss is the worst entry context.**
   Re-entry buckets by gap since previous exit:
   - <6h after a WIN, same side: PF 4.63 (momentum continuation — keep)
   - <6h after a LOSS, opposite side: PF 4.76 (the flip case — keep)
   - **6-24h after a LOSS, same side: PF 0.60** (n=6)
   - >4d after a LOSS, same side: PF 1.08 (n=11)
   13 same-side loss chains cost -$24.6k gross; the Apr-May 2026 streak was
   one 5-loss LONG chain (-$4.5k). v3 adds a **24h same-side cooldown after
   a loss** (config `COOLDOWN_H`). On the trade list it removes 6 trades and
   lifts PF 2.18 -> 2.33; n is small, so treat as a risk rule with weak
   positive evidence, not alpha.
4. **2026 decay is entirely long-side**: 2026 LONGs PF 0.89 (n=12) vs SHORTs
   PF 6.24 (n=7) — confirms the 400h trend filter kept in v2 targets the
   right failure mode.
5. Circuit breaker (4 losses -> 72h pause) replayed on their list: skips 4
   trades, +$1.4k, no DD change — harmless insurance, kept.

## Engineering fixes in v3

- interval bug fixed (above) — the critical one
- **persistent state** (`velocity_state.json`): loss streak, breaker pause,
  PnL cursor, per-side cooldowns, high-water mark all survive restarts; v2
  reset everything on every redeploy and never saw losses booked while down
- **equity floor kill switch**: no new trades below 75% of high-water mark
- **tick-size-conformant SL/TP** (v2 rounded to 4dp; ETHUSDT tick is 0.01)
- `get_closed_pnl` now returns the position side (needed for per-side
  cooldown) and fetches 50 records instead of 10

## Validation status — read before trusting

This session had no network access to Bybit, so the cooldown/flip/slip rules
are validated only against the original's trade list (an approximation: the
one-position-at-a-time rule means removing a trade can unlock entries we
can't observe). Before testnet:

    python backtest_velocity.py        # fetches 900d of ETH 1h, prints
                                       # train 2024-25 / OOS 2026 table with
                                       # one-at-a-time ablations of each rule

Adopt/keep a rule only if the OOS column doesn't degrade. The signal math in
the backtester is identical to the bot (same constants, fill at next bar
open, SL-before-TP intrabar, fees both sides).
