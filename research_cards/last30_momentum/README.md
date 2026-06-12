# Last-30-min momentum study (WS-1.3, Baltussen revalidation) — run log

Stage: harness validated; **question not answerable from owned lake**.

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
