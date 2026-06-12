# Fixing-window study (WS-1.1) — run log

Stage: CANDIDATE-screening infrastructure validated; **question not yet answerable from owned data**.

## Run 2026-06-12 (first end-to-end on real lake)

- Lake: `C:\Users\MSI\Documents\New project\data\npz` (HFT3_NPZ_ROOT), manifest = 315 entries.
- Inventory: `fixing_window_inventory_20260612T130534Z.json` — **3 of 315** entries cover 14:55–15:05 CT
  (lake is macro-event-windowed; events cluster 07:30/12:30/13:00 CT — fixing window almost never captured).
  3 manifest entries reference missing NPZ files (see inventory JSON).
- Covering files (all MES.v.0, `PROP_FLATTEN_TOPSTEP_*_MAIN`, 14:45–15:20 CT windows):
  2023-09-15 (Sep quarterly expiry Fri), 2024-09-18 (FOMC Wed), 2025-06-20 (Jun quarterly expiry Fri).
- Measure: `fixing_window_measure_20260612T130707Z.json` — per-window signed imbalance, 30s fixing VWAP,
  pre-window drift, markouts +30s/+2m/+5m. `oi` is null on all rows (no statistics backfill yet).

## Interpretation

n=3 is far below the gate threshold (n_events ≥ 15, >2 SE). No directional conclusion is drawn or
implied by these three rows. What this run establishes:

1. Harness works end-to-end on real lake data (timezone, aggressor-sign, VWAP, markout logic exercised).
2. **The owned lake cannot answer the WS-1.1 gate.** Required backfill is small and targeted:
   - futures MBO for ~10-minute windows (14:55–15:05 CT) on option-expiry days only — much cheaper
     than full event windows; scope/quote under WS-0.4 before purchase (BudgetManager caps apply).
   - daily statistics (OI by strike/expiry) — covered by Databento Standard plan per ws0-3 memo.
3. OI join (`load_expiry_oi`) stays stubbed until the statistics store exists.

## Next actions

- WS-0.4a: price the expiry-day 10-min MBO pull + statistics backfill; PO needs explicit budget uplift.
- Re-run inventory after any backfill lands; gate evaluation only at n ≥ 15 heavy-expiry days.
