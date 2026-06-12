# Last-30-min momentum study (WS-1.3, Baltussen revalidation) — run log

## GATE VERDICT 2026-06-13: **FAIL — dead permanently** (per plan WS-1.3 rule)

Full-sample run on purchased ES ohlcv-1m (`measure-ohlcv`, 2021-01-01 → 2026-06-12,
1,356 trading days, 339 skipped (Sundays/holidays/missing boundary bars), 1.3-tick
round-trip cost): `last30_momentum_measure_ohlcv_20260612T195000Z.json`

| Year | n | hit rate | mean net ticks/day | t (screening) |
|---|---|---|---|---|
| 2021 | 251 | 0.482 | −0.09 | −0.04 |
| 2022 | 250 | 0.516 | **+6.51** | +1.70 |
| 2023 | 248 | 0.476 | −1.98 | −0.87 |
| 2024 | 249 | 0.490 | −5.48 | −1.74 |
| 2025 | 247 | 0.393 | −8.17 | −2.23 |
| 2026 | 111 | 0.441 | −4.67 | −1.07 |
| **All** | **1,356** | **0.469** | **−2.06** | **−1.53** |

Sign-of-day momentum into the last 30 minutes has NEGATIVE expectancy net of a
1.3-tick cost over the revalidation window; only 2022 was positive, and the
effect has been inverted since 2023. Consequences:
- **WS-1.3 closed: FAIL.** Plan rule: "Fail ⇒ dead permanently."
- **WS-3.2 (last-30-min momentum overlay on the futures lane) is dead** — it was
  conditional on this gate.
- Observation (NOT a licensed hypothesis — post-hoc, would need its own
  pre-registered card): 2025's 39.3% hit rate suggests reversal, not momentum,
  in the recent regime. Recorded for completeness only.

Screening t-stat only (sqrt-n, no DSR/PBO); a negative-mean result needs no
gauntlet to fail the gate.

---

Earlier stage notes (2026-06-12): harness validated; question was not answerable from owned lake.

## Run 2026-06-12

- Inventory: `last30_momentum_inventory_20260612T135224Z.json` — **0 of 315** manifest entries cover
  both the signal window (08:30–14:30 CT) and target window (14:30–15:00 CT).
  133 signal-only (macro-event windows fall inside the morning/midday session),
  3 target-only (the `PROP_FLATTEN_TOPSTEP` 14:45–15:20 CT files), 3 missing NPZ.
- No measure run possible. No conclusion drawn.

## Interpretation

The event-windowed MBO lake structurally cannot host an intraday-session study that needs a
6.5-hour signal window. But this study does not need MBO at all:

- Required input: last trade price at 08:30, 14:30, 15:00 CT per session, 2021→2026.
- Cheapest sufficient schema: Databento GLBX `ohlcv-1m` (or `trades` filtered) on ES.v.0 —
  ~1,300 sessions × 1-minute bars ≈ negligible GB; orders of magnitude below the MBO caps.
- Harness extension needed: an OHLCV adapter (current measure path reads NPZ MBO).

## Next actions

- Include ES ohlcv-1m 2021-01-01→2026 in the WS-0.4 backfill plan (near-zero cost line item).
- After backfill: add ohlcv input path to `last30_momentum_study.py`, run measure, evaluate the
  WS-1.3 gate (positive mean net ticks at 1.3-tick round-trip cost, year-stable). Fail ⇒ dead permanently.
